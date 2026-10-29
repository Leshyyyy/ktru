"""Импорт каталога КТРУ из JSONL (одна строка — CatalogRow) в catalog_versions и catalog_items.

Этим же кодом будет работать POST /catalog/import (FR-8.1); сейчас его вызывает migrate для стартового каталога.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from contracts.ml_contracts import CatalogRow

from .models import CatalogItem, CatalogVersion

BATCH_SIZE = 500
_UPDATABLE = ("okpd2_code", "name", "description", "unit", "status", "replaced_by", "characteristics", "search_text")


class CatalogImportError(Exception):
    """Файл каталога не подходит для импорта. Текст — для человека."""


def build_search_text(row: CatalogRow) -> str:
    """Текст для полнотекстового индекса (catalog_items.tsv): наименование, описание, названия характеристик
    и их текстовые значения.

    Числа не берём, как и ML в ml/catalog.py: внутри одного ОКПД2 позиции различаются числами, а их сравнивает
    сверка характеристик, а не текстовый поиск. CatalogIndexer у ML может пересобрать поле при индексации.
    """
    parts = [row.name]
    if row.description:
        parts.append(row.description)
    for characteristic in row.characteristics:
        name = characteristic.get("name")
        if isinstance(name, str):
            parts.append(name)
        for value in characteristic.get("values") or []:
            if isinstance(value, str) and value.strip() not in ("Да", "Нет") and not any(c.isdigit() for c in value):
                parts.append(value)
    return " ".join(" ".join(parts).split())


def read_rows(path: Path) -> Iterator[CatalogRow]:
    with path.open(encoding="utf-8") as f:
        for number, line in enumerate(f, start=1):
            if not line.strip():
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError as e:
                raise CatalogImportError(f"Строка {number}: некорректный JSON ({e.msg}).") from e
            try:
                yield CatalogRow.model_validate(data)
            except ValidationError as e:
                fields = ", ".join(".".join(str(p) for p in err["loc"]) or "строка" for err in e.errors())
                message = f"Строка {number}: не соответствует формату CatalogRow (поля: {fields})."
                raise CatalogImportError(message) from e


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def import_catalog_file(session: Session, path: Path, *, source: str, note: str | None = None) -> CatalogVersion:
    """Создаёт версию каталога и загружает строки в транзакции вызывающего: ошибка — откат целиком.

    Повтор кода внутри файла обновляет строку (последняя побеждает), как и повторная загрузка позиции.
    """
    version = CatalogVersion(source=source, status="importing", note=note)
    session.add(version)
    session.flush()

    batch: dict[str, dict[str, Any]] = {}
    for row in read_rows(path):
        batch[row.ktru_code] = _values(version.id, row)
        if len(batch) >= BATCH_SIZE:
            _upsert(session, batch)
            batch = {}
    if batch:
        _upsert(session, batch)

    count = session.scalar(select(func.count()).select_from(CatalogItem).where(CatalogItem.version_id == version.id))
    if not count:
        raise CatalogImportError("В файле нет ни одной позиции каталога.")
    version.items_count = count
    version.status = "ready"
    session.flush()
    return version


def _values(version_id: uuid.UUID, row: CatalogRow) -> dict[str, Any]:
    return {
        "version_id": version_id,
        "ktru_code": row.ktru_code,
        "okpd2_code": row.okpd2_code,
        "name": row.name,
        "description": row.description,
        "unit": row.unit,
        "status": row.status,
        "replaced_by": row.replaced_by,
        "characteristics": row.characteristics,
        "search_text": build_search_text(row),
    }


def _upsert(session: Session, batch: dict[str, dict[str, Any]]) -> None:
    stmt = insert(CatalogItem).values(list(batch.values()))
    stmt = stmt.on_conflict_do_update(
        index_elements=[CatalogItem.version_id, CatalogItem.ktru_code],
        set_={name: stmt.excluded[name] for name in _UPDATABLE},
    )
    session.execute(stmt)
