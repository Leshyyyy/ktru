"""Характеристики итогового кода: одна SQL-выборка для карточки, фильтра, сводки и экспорта.

Каталог задаёт полный набор и порядок характеристик. Сверка берётся у выбранного кандидата,
ручные значения — из журнала именно этого кода. Сброс (NULL) не удаляет историю.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import Integer, Text, Uuid, bindparam, select, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Session
from sqlalchemy.sql.selectable import Subquery

# Только фиксированные SQL-фрагменты; пользовательские UUID передаются bind-параметрами.
_SELECT = """
SELECT i.id AS item_id, i.job_id, final.ktru_code, final.name,
       CASE WHEN final.ktru_code IS NULL THEN NULL
            WHEN p.id IS NOT NULL THEN 'prediction' ELSE 'catalog' END AS source,
       CASE WHEN final.ktru_code IS NULL THEN NULL ELSE
           count(*) FILTER (WHERE effective.required AND effective.status = 'missing')::int
       END AS missing_required_count,
       coalesce(jsonb_agg(jsonb_build_object(
           'name', c.value->>'name', 'required', effective.required,
           'expected', coalesce(m.value->>'expected',
               (SELECT string_agg(v, '; ') FROM jsonb_array_elements_text(c.value->'values') AS vals(v))),
           'allowed_values', coalesce(c.value->'values', '[]'::jsonb),
           'found', m.value->>'found', 'value', manual.value, 'status', effective.status
       ) ORDER BY effective.required DESC, c.ordinality)
           FILTER (WHERE c.value IS NOT NULL AND
               (effective.required OR m.value->>'found' IS NOT NULL OR manual.value IS NOT NULL)),
           '[]'::jsonb) AS characteristics,
       coalesce(array_agg(c.value->>'name') FILTER (WHERE c.value IS NOT NULL), '{}') AS allowed_names
FROM items i
JOIN jobs j ON j.id = i.job_id
JOIN v_item_final final ON final.item_id = i.id
LEFT JOIN catalog_items ci ON ci.version_id = j.catalog_version_id AND ci.ktru_code = final.ktru_code
LEFT JOIN LATERAL (
    SELECT id, characteristic_matches FROM predictions
    WHERE item_id = i.id AND ktru_code = final.ktru_code ORDER BY rank LIMIT 1
) p ON true
LEFT JOIN LATERAL jsonb_array_elements(ci.characteristics) WITH ORDINALITY c(value, ordinality) ON true
LEFT JOIN LATERAL (
    SELECT value FROM jsonb_array_elements(p.characteristic_matches)
    WHERE value->>'name' = c.value->>'name' LIMIT 1
) m ON true
LEFT JOIN LATERAL (
    SELECT value FROM item_characteristic_values
    WHERE item_id = i.id AND ktru_code = final.ktru_code AND name = c.value->>'name'
    ORDER BY created_at DESC, id DESC LIMIT 1
) manual ON true
CROSS JOIN LATERAL (
    SELECT coalesce((c.value->>'required')::boolean, false) AS required,
           CASE WHEN manual.value IS NOT NULL THEN 'filled'
                ELSE coalesce(m.value->>'status', 'missing') END AS status
) effective
"""


# Для сводки и фильтра достаточно факта «есть незаполненная обязательная характеристика»: полный jsonb
# характеристик и список имён там не нужны. EXISTS прекращает перебор на первом пропуске и не собирает JSONB.
_COUNT = """
SELECT i.id AS item_id, i.job_id,
       (EXISTS (
           SELECT 1
           FROM jsonb_array_elements(coalesce(ci.characteristics, '[]'::jsonb)) c(value)
           LEFT JOIN LATERAL (
               SELECT value FROM item_characteristic_values
               WHERE item_id = i.id AND ktru_code = final.ktru_code AND name = c.value->>'name'
               ORDER BY created_at DESC, id DESC LIMIT 1
           ) manual ON true
           LEFT JOIN LATERAL (
               SELECT value FROM jsonb_array_elements(coalesce(p.characteristic_matches, '[]'::jsonb))
               WHERE value->>'name' = c.value->>'name' LIMIT 1
           ) m ON true
           WHERE coalesce((c.value->>'required')::boolean, false)
             AND manual.value IS NULL
             AND coalesce(m.value->>'status', 'missing') = 'missing'
       ))::int AS missing_required_count
FROM items i
JOIN jobs j ON j.id = i.job_id
JOIN v_item_final final ON final.item_id = i.id
LEFT JOIN catalog_items ci ON ci.version_id = j.catalog_version_id AND ci.ktru_code = final.ktru_code
LEFT JOIN LATERAL (
    SELECT characteristic_matches FROM predictions
    WHERE item_id = i.id AND ktru_code = final.ktru_code ORDER BY rank LIMIT 1
) p ON true
"""


def characteristic_counts(
    *, item_ids: Sequence[uuid.UUID] | None = None, job_ids: Sequence[uuid.UUID] | None = None
) -> Subquery:
    """Одна строка на позицию: 1 — есть незаполненная обязательная характеристика, иначе 0.

    В отличие от `characteristic_state`, не собирает JSONB и имена: нужен только счётчик. Позиции без кода
    получают 0 (а не NULL), потому что используется единственная проверка `> 0`.
    """
    if item_ids is not None:
        scope, ids = "i.id", item_ids
    elif job_ids is not None:
        scope, ids = "i.job_id", job_ids
    else:
        raise ValueError("Нужны идентификаторы позиций или задач")
    return (
        text(_COUNT + f" WHERE {scope} IN :ids")
        .bindparams(bindparam("ids", value=list(ids), expanding=True, type_=Uuid, unique=True))
        .columns(item_id=Uuid, job_id=Uuid, missing_required_count=Integer)
        .subquery()
    )


def characteristic_state(
    *, item_ids: Sequence[uuid.UUID] | None = None, job_ids: Sequence[uuid.UUID] | None = None
) -> Subquery:
    """Одна строка на позицию, включая позиции без кода. Всегда ограничена страницей или задачами."""
    if item_ids is not None:
        scope, ids = "i.id", item_ids
    elif job_ids is not None:
        scope, ids = "i.job_id", job_ids
    else:
        raise ValueError("Нужны идентификаторы позиций или задач")
    return (
        text(_SELECT + f" WHERE {scope} IN :ids GROUP BY i.id, i.job_id, final.ktru_code, final.name, p.id")
        .bindparams(bindparam("ids", value=list(ids), expanding=True, type_=Uuid, unique=True))
        .columns(
            item_id=Uuid,
            job_id=Uuid,
            ktru_code=Text,
            name=Text,
            source=Text,
            missing_required_count=Integer,
            characteristics=JSONB,
            allowed_names=ARRAY(Text),
        )
        .subquery()
    )


def load_characteristics(session: Session, item_ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, dict[str, Any]]:
    if not item_ids:
        return {}
    return {
        row["item_id"]: dict(row) for row in session.execute(select(characteristic_state(item_ids=item_ids))).mappings()
    }
