"""Тег catalog (чтение): версии каталога, позиция КТРУ с характеристиками и поиск для ручной правки (FR-6.2).

Без version_id работает последняя версия в статусе ready — та же, что получают новые задачи. При правке позиции
фронт передаёт catalog_version_id задачи: тогда предлагаются только коды, которые примет PUT /items/{id}/decision.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from backend.core import models
from backend.core.catalog import latest_ready_version

from .. import schemas
from ..deps import get_db
from ..errors import ApiError, not_found, not_ready
from ..views import escape_like

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
    Сверху — совпадение по коду, затем действующие коды и самые похожие наименования.
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
    return schemas.CatalogVersionList(
        items=[
            schemas.CatalogVersion(
                version_id=v.id,
                source=v.source,
                status=v.status,
                items_count=v.items_count,
                note=v.note,
                loaded_at=v.loaded_at,
            )
            for v in versions
        ]
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
