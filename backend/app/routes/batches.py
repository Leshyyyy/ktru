"""Комплекты обычных задач: атомарное создание, общая сводка и выгрузка."""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.core import models
from backend.core.catalog import latest_ready_version
from backend.core.processing import ACTIVE_STATUSES
from backend.core.queue import TaskQueue
from backend.core.settings import Settings
from backend.files import UnreviewedItemsError, export_batch_results

from .. import schemas
from ..deps import get_db, get_health_checker, get_queue, get_settings
from ..errors import ApiError, invalid_state, not_found, not_ready
from ..health import HealthChecker
from ..views import batch_views
from .export import _export_items
from .jobs import enqueue_job, prepare_job

log = logging.getLogger(__name__)
router = APIRouter(tags=["batches"])

SettingsDep = Annotated[Settings, Depends(get_settings)]
SessionDep = Annotated[Session, Depends(get_db)]


@router.post("/batches", status_code=202, response_model=schemas.Batch)
def create_batch(
    body: schemas.BatchCreate,
    settings: SettingsDep,
    session: SessionDep,
    queue: Annotated[TaskQueue, Depends(get_queue)],
    checker: Annotated[HealthChecker, Depends(get_health_checker)],
) -> schemas.Batch:
    if len(body.jobs) > settings.max_files_per_batch:
        raise ApiError(
            422,
            "limit_exceeded",
            f"В одном комплекте можно загрузить не больше {settings.max_files_per_batch} файлов.",
            {"max_files_per_batch": settings.max_files_per_batch},
        )
    if not checker.check().ready:
        raise not_ready()
    version_id = latest_ready_version(session)
    if version_id is None:
        raise not_ready()
    batch_id = uuid.uuid4()
    session.add(models.Batch(id=batch_id, name=body.name))
    job_ids = []
    created_at = datetime.now(UTC)
    try:
        for index, job in enumerate(body.jobs):
            try:
                job_id, _ = prepare_job(
                    session,
                    job,
                    batch_id=batch_id,
                    version_id=version_id,
                    created_at=created_at + timedelta(microseconds=index),
                )
                job_ids.append(job_id)
            except ApiError as error:
                details = dict(error.details or {})
                details["job_index"] = index
                raise ApiError(error.status, error.code, error.message, details, error.headers) from error
        session.commit()
    except Exception:
        session.rollback()
        raise
    for job_id in job_ids:
        enqueue_job(queue, job_id)
    log.info("Комплект создан", extra={"batch_id": str(batch_id), "jobs": len(job_ids)})
    return batch_views(session, settings, [batch_id])[0]


@router.get("/batches", response_model=schemas.BatchList)
def list_batches(
    settings: SettingsDep,
    session: SessionDep,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 50,
) -> schemas.BatchList:
    total = session.scalar(select(func.count()).select_from(models.Batch)) or 0
    ids = session.scalars(
        select(models.Batch.id)
        .order_by(models.Batch.created_at.desc(), models.Batch.id.desc())
        .limit(page_size)
        .offset((page - 1) * page_size)
    ).all()
    return schemas.BatchList(items=batch_views(session, settings, ids), total=total, page=page, page_size=page_size)


@router.get("/batches/{batch_id}", response_model=schemas.Batch)
def get_batch(batch_id: uuid.UUID, settings: SettingsDep, session: SessionDep) -> schemas.Batch:
    views = batch_views(session, settings, [batch_id])
    if not views:
        raise not_found("batch", "Комплект не найден.")
    return views[0]


@router.get("/batches/{batch_id}/export", response_class=Response)
def export_batch(
    batch_id: uuid.UUID,
    session: SessionDep,
    export_format: Annotated[Literal["xlsx", "csv", "json", "application"], Query(alias="format")] = "xlsx",
    allow_unreviewed: bool = True,
) -> Response:
    if session.get(models.Batch, batch_id) is None:
        raise not_found("batch", "Комплект не найден.")
    rows = session.execute(
        select(models.Job, models.SourceFile.filename)
        .join(models.SourceFile, models.SourceFile.id == models.Job.file_id)
        .where(models.Job.batch_id == batch_id)
        .order_by(models.Job.created_at, models.Job.id)
    ).all()
    if any(job.status in ACTIVE_STATUSES for job, _ in rows):
        raise invalid_state("Комплект ещё обрабатывается.", status="processing")
    items: list[dict[str, Any]] = []
    for job, filename in rows:
        items.extend(
            {**item, "source_filename": filename} for item in _export_items(session, job.id, job.column_mapping)
        )
    try:
        result = export_batch_results(
            items,
            export_format=export_format,
            batch_filename="Комплект.xlsx",
            allow_unreviewed=allow_unreviewed,
        )
    except UnreviewedItemsError as error:
        details = error.details if isinstance(error.details, dict) else None
        raise ApiError(409, "unreviewed_items", error.message, details) from None
    log.info(
        "Комплект выгружен", extra={"batch_id": str(batch_id), "format": export_format, "bytes": len(result.content)}
    )
    return Response(
        content=result.content,
        media_type=result.media_type,
        headers={
            "Content-Disposition": result.content_disposition,
            "X-Unreviewed-Count": str(result.unreviewed_count),
            "X-Missing-Required-Count": str(result.missing_required_count),
        },
    )
