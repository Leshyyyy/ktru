"""Тег items (чтение): позиции задачи с фильтрами и карточка позиции (FR-5.1–FR-5.4).

Позиции доступны во время обработки: уже обработанные приходят с предсказаниями, остальные — в статусе pending.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from backend.core import models
from backend.core.settings import Settings

from .. import schemas
from ..deps import get_db, get_settings
from ..errors import not_found
from ..views import ItemFilters, item_page, item_views

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
