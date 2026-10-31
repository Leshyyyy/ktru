"""Тег items: позиции задачи с фильтрами и карточка (FR-5.1–FR-5.4), решения человека (FR-6.1–FR-6.4).

Позиции доступны во время обработки: уже обработанные приходят с предсказаниями, остальные — в статусе pending.
Решения только добавляются в журнал decisions; итоговый код — последнее решение, иначе rank 1 (v_item_final).
Ручное решение (via = manual) пишет пару «текст → код» в confirmed_examples, массовое (via = bulk) — нет:
иначе модель училась бы на собственных непроверенных ответах.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import and_, exists, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, aliased

from backend.core import models
from backend.core.settings import Settings
from contracts.ml_contracts import normalize_text

from .. import schemas
from ..deps import get_db, get_settings
from ..errors import invalid_state, not_found
from ..views import ItemFilters, item_page, item_views, real_literal

router = APIRouter(tags=["items"])

SettingsDep = Annotated[Settings, Depends(get_settings)]
SessionDep = Annotated[Session, Depends(get_db)]


@router.get("/jobs/{job_id}/items", response_model=schemas.ItemList)
def list_items(
    job_id: uuid.UUID,
    settings: SettingsDep,
    session: SessionDep,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 50,
    band: schemas.Band | None = None,
    status: schemas.ItemStatus | None = None,
    q: Annotated[str | None, Query(max_length=200)] = None,
    flag: schemas.ItemFlag | None = None,
    group_id: uuid.UUID | None = None,
    sort: Literal["row_index", "confidence_asc"] = "row_index",
) -> schemas.ItemList:
    if not session.scalar(select(exists().where(models.Job.id == job_id))):
        raise not_found("job", "Задача не найдена.")
    filters = ItemFilters(band=band, status=status, q=q, flag=flag, group_id=group_id, sort=sort)
    return item_page(session, settings, job_id, filters, page, page_size)


@router.get("/items/{item_id}", response_model=schemas.Item)
def get_item(item_id: uuid.UUID, settings: SettingsDep, session: SessionDep) -> schemas.Item:
    views = item_views(session, settings, [item_id])
    if not views:
        raise not_found("item", "Позиция не найдена.")
    return views[0]


# --- Решения --------------------------------------------------------------------------------------------------

_NO_CODE = {
    "pending": "Позиция ещё не обработана. Дождитесь результата и повторите.",
    "failed": "У позиции нет предложенного кода: обработка завершилась ошибкой. Укажите код вручную.",
}


@router.post("/items/{item_id}/confirm", response_model=schemas.Item)
def confirm_item(item_id: uuid.UUID, settings: SettingsDep, session: SessionDep) -> schemas.Item:
    """Решение confirm для предсказания rank 1. Повторный вызов ничего не меняет: фронт может прислать его дважды."""
    item = _lock_item(session, item_id)
    top = _top_code(session, item.id)
    if top is None:
        message = _NO_CODE.get(item.status, "У позиции нет предложенного кода. Укажите код вручную.")
        raise invalid_state(message, status=item.status)
    latest = _latest_decision(session, item.id)
    if not _same_decision(latest, "confirm", top, None):
        _decide(session, item, kind="confirm", code=top, reason=None, old_code=latest.ktru_code if latest else top)
        session.commit()
    return item_views(session, settings, [item_id])[0]


@router.put("/items/{item_id}/decision", response_model=schemas.Item)
def correct_item(
    item_id: uuid.UUID, body: schemas.DecisionCreate, settings: SettingsDep, session: SessionDep
) -> schemas.Item:
    """Код вручную: совпал с rank 1 — это подтверждение, иначе правка. Работает и для упавших позиций."""
    item = _lock_item(session, item_id)
    if item.status == "pending":
        # Воркер пишет только позиции в pending: решение до него он бы не перезаписал, но и модель бы не спросили.
        raise invalid_state(_NO_CODE["pending"], status=item.status)
    code = body.ktru_code.strip()
    version_id = session.scalar(select(models.Job.catalog_version_id).where(models.Job.id == item.job_id))
    known = session.scalar(
        select(exists().where(models.CatalogItem.version_id == version_id, models.CatalogItem.ktru_code == code))
    )
    if not known:
        raise not_found("ktru_code", f"Кода {code} нет в версии каталога, с которой работает задача.")
    top = _top_code(session, item.id)
    kind = "confirm" if code == top else "correct"
    reason = body.reason.strip() if body.reason and body.reason.strip() else None
    latest = _latest_decision(session, item.id)
    if not _same_decision(latest, kind, code, reason):
        _decide(session, item, kind=kind, code=code, reason=reason, old_code=latest.ktru_code if latest else top)
        session.commit()
    return item_views(session, settings, [item_id])[0]


@router.post("/jobs/{job_id}/confirm-bulk", response_model=schemas.BulkConfirmResult)
def confirm_bulk(job_id: uuid.UUID, body: schemas.BulkConfirm, session: SessionDep) -> schemas.BulkConfirmResult:
    """Подтверждает processed и needs_review с уверенностью rank 1 не ниже порога (FR-6.3). Можно во время
    обработки: позиции в pending не затрагиваются. Строки блокируются — ручное решение не потеряется в гонке.
    """
    if not session.scalar(select(exists().where(models.Job.id == job_id))):
        raise not_found("job", "Задача не найдена.")
    top = aliased(models.Prediction)
    conditions: list[Any] = [
        models.Item.job_id == job_id,
        models.Item.status.in_(("processed", "needs_review")),
        top.confidence >= real_literal(body.min_confidence),
    ]
    if body.skip_flagged:
        conditions.append(func.cardinality(models.Item.flags) == 0)
    rows = session.execute(
        select(models.Item.id, top.ktru_code)
        .join(top, and_(top.item_id == models.Item.id, top.rank == 1))
        .where(*conditions)
        .with_for_update(of=models.Item)
    ).all()
    if rows:
        session.execute(
            insert(models.Decision),
            [
                {"id": uuid.uuid4(), "item_id": i, "kind": "confirm", "via": "bulk", "ktru_code": c, "old_code": c}
                for i, c in rows
            ],
        )
        session.execute(update(models.Item).where(models.Item.id.in_([i for i, _ in rows])).values(status="confirmed"))
        session.commit()
    return schemas.BulkConfirmResult(confirmed_count=len(rows))


def _lock_item(session: Session, item_id: uuid.UUID) -> models.Item:
    # Блокировка строки: два решения по одной позиции и запись воркера идут по очереди.
    item = session.scalar(select(models.Item).where(models.Item.id == item_id).with_for_update())
    if item is None:
        raise not_found("item", "Позиция не найдена.")
    return item


def _top_code(session: Session, item_id: uuid.UUID) -> str | None:
    return session.scalar(
        select(models.Prediction.ktru_code).where(models.Prediction.item_id == item_id, models.Prediction.rank == 1)
    )


def _latest_decision(session: Session, item_id: uuid.UUID) -> Any:
    # Тот же порядок, что в v_item_final: последнее по created_at (clock_timestamp) решение побеждает.
    return session.execute(
        select(models.Decision.kind, models.Decision.via, models.Decision.ktru_code, models.Decision.reason)
        .where(models.Decision.item_id == item_id)
        .order_by(models.Decision.created_at.desc())
        .limit(1)
    ).first()


def _same_decision(latest: Any, kind: str, code: str, reason: str | None) -> bool:
    """Повтор того же ручного решения. Массовое подтверждение не в счёт: ручное после него — уже проверка человеком."""
    return (
        latest is not None
        and latest.via == "manual"
        and latest.kind == kind
        and latest.ktru_code == code
        and (reason is None or reason == latest.reason)
    )


def _decide(
    session: Session, item: models.Item, *, kind: str, code: str, reason: str | None, old_code: str | None
) -> None:
    session.add(
        models.Decision(
            id=uuid.uuid4(), item_id=item.id, kind=kind, via="manual", ktru_code=code, old_code=old_code, reason=reason
        )
    )
    item.status = "confirmed" if kind == "confirm" else "corrected"
    # Одна позиция — один пример: новое решение меняет код примера (UNIQUE source_item_id в schema.sql).
    # created_at сдвигается — по нему ML сможет досылать в кэш только изменившиеся примеры.
    stmt = insert(models.ConfirmedExample).values(
        id=uuid.uuid4(),
        text=item.raw_text,
        norm_text=normalize_text(item.raw_text),
        ktru_code=code,
        source_item_id=item.id,
    )
    session.execute(
        stmt.on_conflict_do_update(
            index_elements=[models.ConfirmedExample.source_item_id],
            set_={"ktru_code": stmt.excluded.ktru_code, "created_at": func.now()},
        )
    )
