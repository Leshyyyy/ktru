"""Тег catalog: версии каталога, позиция КТРУ с характеристиками и поиск для ручной правки (FR-6.2), импорт новой
версии без перезапуска (FR-8.1, порядок шагов — в backend/core/catalog.py).

Без version_id работает последняя версия в статусе ready — та же, что получают новые задачи. При правке позиции
фронт передаёт catalog_version_id задачи: тогда предлагаются только коды, которые примет PUT /items/{id}/decision.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Form, Query, UploadFile
from pydantic import ValidationError
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from backend.core import models, uploads
from backend.core.catalog import IMPORT_SOURCE, fail_version, latest_ready_version
from backend.core.queue import TaskQueue
from backend.core.settings import Settings
from contracts.ml_contracts import CatalogRow

from .. import schemas
from ..deps import get_db, get_queue, get_settings
from ..errors import ApiError, file_too_large, not_found, not_ready
from ..views import escape_like

log = logging.getLogger(__name__)
router = APIRouter(tags=["catalog"])

SessionDep = Annotated[Session, Depends(get_db)]


@router.get("/catalog/search", response_model=schemas.CatalogSearchResult)
def search_catalog(
    session: SessionDep,
    q: Annotated[str, Query(min_length=2, max_length=200)],
    version_id: uuid.UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
) -> schemas.CatalogSearchResult:
    """По началу кода и по наименованию: подстрока (индекс триграмм) или слова с учётом формы (tsv, «ноутбуки»).
    Сверху — совпадение по коду, затем действующие коды и самые похожие наименования. Укрупнённые позиции
    не предлагаются: ответом они быть не должны.
    """
    text = q.strip()
    if len(text) < 2:  # «  а» проходит min_length, но искать по одной букве бессмысленно
        message = "Некорректный запрос: поле q должно быть не короче 2 символов."
        raise ApiError(400, "invalid_request", message, {"field": "q"})
    version = _version(session, version_id)
    item = models.CatalogItem
    by_code = item.ktru_code.like(f"{escape_like(text)}%", escape="\\")
    rows = session.scalars(
        select(item)
        .where(
            item.version_id == version,
            item.is_enlarged.is_(False),
            or_(
                by_code,
                item.name.ilike(f"%{escape_like(text)}%", escape="\\"),
                item.tsv.op("@@")(func.plainto_tsquery("russian", text)),
            ),
        )
        .order_by(by_code.desc(), item.status == "archived", func.similarity(item.name, text).desc(), item.ktru_code)
        .limit(limit)
    ).all()
    return schemas.CatalogSearchResult(items=[catalog_item(row) for row in rows])


@router.get("/catalog/items/{ktru_code}", response_model=schemas.CatalogItem)
def get_catalog_item(ktru_code: str, session: SessionDep, version_id: uuid.UUID | None = None) -> schemas.CatalogItem:
    version = _version(session, version_id)
    row = session.scalar(
        select(models.CatalogItem).where(
            models.CatalogItem.version_id == version, models.CatalogItem.ktru_code == ktru_code
        )
    )
    if row is None:
        raise not_found("ktru_code", f"Кода {ktru_code} нет в каталоге.")
    return catalog_item(row)


@router.get("/catalog/versions", response_model=schemas.CatalogVersionList)
def list_versions(session: SessionDep) -> schemas.CatalogVersionList:
    versions = session.scalars(select(models.CatalogVersion).order_by(models.CatalogVersion.loaded_at.desc())).all()
    return schemas.CatalogVersionList(items=[catalog_version(v) for v in versions])


@router.post("/catalog/import", status_code=202, response_model=schemas.CatalogVersion)
def import_catalog(
    file: UploadFile,
    settings: Annotated[Settings, Depends(get_settings)],
    session: SessionDep,
    queue: Annotated[TaskQueue, Depends(get_queue)],
    note: Annotated[str | None, Form(max_length=500)] = None,
) -> schemas.CatalogVersion:
    """Файл сохраняется, строки загружает worker-io, индекс строит worker-ml; статус — в GET /catalog/versions."""
    _check_first_row(file)
    version_id = uuid.uuid4()
    try:
        stored = uploads.save_upload(
            file.file, settings.upload_dir, version_id, "jsonl", settings.catalog_import_max_mb * 1024 * 1024
        )
    except uploads.UploadTooLarge:
        raise file_too_large(settings.catalog_import_max_mb) from None
    version = models.CatalogVersion(
        id=version_id,
        source=f"{IMPORT_SOURCE}{uploads.clean_filename(file.filename)}",
        status="importing",
        note=note or None,
    )
    session.add(version)
    session.commit()
    try:
        queue.import_catalog(str(version_id), stored.storage_path)
    except Exception:
        log.exception("Не удалось поставить импорт каталога в очередь", extra={"version_id": str(version_id)})
        fail_version(session.get_bind(), version_id, "очередь недоступна, загрузите файл заново.")
        uploads.remove_upload(settings.upload_dir, stored.storage_path)
    session.refresh(version)
    log.info("Импорт каталога запущен", extra={"version_id": str(version_id), "size_bytes": stored.size_bytes})
    return catalog_version(version)


def _check_first_row(file: UploadFile) -> None:
    """Первая непустая строка — CatalogRow: явно чужой файл отклоняется сразу, остальное проверит worker-io."""
    first = b""
    for line in file.file:
        if line.strip():
            first = line
            break
    file.file.seek(0)
    try:
        CatalogRow.model_validate(json.loads(first))
    except (ValueError, ValidationError):
        message = "Файл не похож на каталог: нужен JSONL, одна позиция КТРУ на строку (формат CatalogRow)."
        raise ApiError(422, "file_unreadable", message) from None


def catalog_version(v: models.CatalogVersion) -> schemas.CatalogVersion:
    return schemas.CatalogVersion(
        version_id=v.id, source=v.source, status=v.status, items_count=v.items_count, note=v.note, loaded_at=v.loaded_at
    )


def _version(session: Session, version_id: uuid.UUID | None) -> uuid.UUID:
    if version_id is not None:
        if session.get(models.CatalogVersion, version_id) is None:
            raise not_found("catalog_version", "Версия каталога не найдена.")
        return version_id
    latest = latest_ready_version(session)
    if latest is None:
        raise not_ready()
    return latest


def catalog_item(row: models.CatalogItem) -> schemas.CatalogItem:
    """CatalogRow.characteristics контракт не проверяет: значения приводим к строкам, записи без названия пропускаем."""
    characteristics = []
    for raw in row.characteristics or []:
        if not isinstance(raw, dict) or not isinstance(raw.get("name"), str):
            continue
        values: Any = raw.get("values") or []
        characteristics.append(
            schemas.CatalogCharacteristic(
                name=raw["name"],
                values=[str(v) for v in values] if isinstance(values, list) else [str(values)],
                required=bool(raw.get("required", False)),
            )
        )
    return schemas.CatalogItem(
        ktru_code=row.ktru_code,
        okpd2_code=row.okpd2_code,
        name=row.name,
        description=row.description,
        unit=row.unit,
        characteristics=characteristics,
    )
