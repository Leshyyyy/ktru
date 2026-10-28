"""Тег jobs: создание задачи, список, статус и прогресс, отмена, удаление (FR-2.1, FR-2.2, FR-2.4).

POST /jobs только записывает задачу и ставит io.parse_file в очередь: файл разбирает worker-io, позиции
пачками считает worker-ml (backend/core/processing.py).
"""

from __future__ import annotations

import logging
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import delete, exists, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from backend.core import models, uploads
from backend.core.processing import ACTIVE_STATUSES
from backend.core.queue import TaskQueue
from backend.core.settings import Settings
from backend.files import MappingInvalidError
from backend.files.hash import compute_headers_hash
from backend.files.parser import validate_column_mapping

from .. import schemas
from ..deps import get_db, get_health_checker, get_queue, get_settings
from ..errors import ApiError, file_error, invalid_state, mapping_invalid, not_found, not_ready
from ..health import HealthChecker
from ..views import job_views

log = logging.getLogger(__name__)

router = APIRouter(tags=["jobs"])

SettingsDep = Annotated[Settings, Depends(get_settings)]
SessionDep = Annotated[Session, Depends(get_db)]


@router.post("/jobs", status_code=202, response_model=schemas.Job)
def create_job(
    body: schemas.JobCreate,
    settings: SettingsDep,
    session: SessionDep,
    queue: Annotated[TaskQueue, Depends(get_queue)],
    checker: Annotated[HealthChecker, Depends(get_health_checker)],
) -> schemas.Job:
    if not checker.check().ready:
        raise not_ready()
    source = session.get(models.SourceFile, body.file_id)
    if source is None:
        raise not_found("file", "Файл не найден. Загрузите его заново.")
    sheet = _sheet(source, body.sheet)
    columns = [entry.model_dump() for entry in body.column_mapping]
    _check_mapping(sheet, columns)
    version_id = session.scalar(
        select(models.CatalogVersion.id)
        .where(models.CatalogVersion.status == "ready")
        .order_by(models.CatalogVersion.loaded_at.desc())
        .limit(1)
    )
    if version_id is None:
        raise not_ready()

    job_id = uuid.uuid4()
    session.add(
        models.Job(
            id=job_id,
            file_id=source.id,
            catalog_version_id=version_id,
            status="queued",
            column_mapping={"sheet": sheet["name"], "header_row": body.header_row, "columns": columns},
            options=body.options.model_dump(),
        )
    )
    _remember_layout(session, sheet, body.header_row, columns)
    session.commit()
    try:
        queue.parse_file(str(job_id))
    except Exception:
        # Задача уже в базе в статусе queued: через 5 минут её снова поставит в очередь watchdog.
        log.exception("Не удалось поставить разбор файла в очередь", extra={"job_id": str(job_id)})
    log.info("Задача создана", extra={"job_id": str(job_id), "file_id": str(source.id)})
    return job_views(session, settings, [job_id])[0]


def _sheet(source: models.SourceFile, name: str | None) -> dict[str, Any]:
    sheets = source.parsed_meta.get("sheets", [])
    for sheet in sheets:
        if name is None or sheet["name"] == name:
            return sheet
    raise mapping_invalid(f"Лист «{name}» не найден в файле.", sheet=name)


def _check_mapping(sheet: dict[str, Any], columns: list[dict[str, Any]]) -> None:
    """Колонки есть на листе и не повторяются; правила ролей (ровно одна name и т. д.) — в backend/files."""
    headers = [column["header"] for column in sheet["columns"]]
    seen: set[int] = set()
    for column in columns:
        index = column["index"]
        if index >= len(headers):
            raise mapping_invalid(f"На листе «{sheet['name']}» нет колонки №{index + 1}.", index=index)
        if index in seen:
            raise mapping_invalid(f"Колонка «{headers[index]}» указана в сопоставлении дважды.", index=index)
        seen.add(index)
    try:
        validate_column_mapping(columns)
    except MappingInvalidError as e:
        raise file_error(*uploads.file_error(e)) from None


def _remember_layout(session: Session, sheet: dict[str, Any], header_row: int, columns: list[dict[str, Any]]) -> None:
    """file_layouts: ключ — заголовки, какими их увидел POST /files, значение — что подтвердил пользователь.
    Следующий файл с теми же заголовками получит это сопоставление в layout_hint.
    """
    header_hash = compute_headers_hash([column["header"] for column in sheet["columns"]])
    stmt = insert(models.FileLayout).values(header_hash=header_hash, header_row=header_row, column_mapping=columns)
    session.execute(
        stmt.on_conflict_do_update(
            index_elements=[models.FileLayout.header_hash],
            set_={
                "header_row": stmt.excluded.header_row,
                "column_mapping": stmt.excluded.column_mapping,
                "updated_at": func.now(),
            },
        )
    )


@router.get("/jobs", response_model=schemas.JobList)
def list_jobs(
    settings: SettingsDep,
    session: SessionDep,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 50,
) -> schemas.JobList:
    total = session.scalar(select(func.count()).select_from(models.Job)) or 0
    ids = session.scalars(
        select(models.Job.id)
        .order_by(models.Job.created_at.desc(), models.Job.id)
        .limit(page_size)
        .offset((page - 1) * page_size)
    ).all()
    return schemas.JobList(items=job_views(session, settings, ids), total=total, page=page, page_size=page_size)


@router.get("/jobs/{job_id}", response_model=schemas.Job)
def get_job(job_id: uuid.UUID, settings: SettingsDep, session: SessionDep) -> schemas.Job:
    views = job_views(session, settings, [job_id])
    if not views:
        raise _job_not_found()
    return views[0]


@router.post("/jobs/{job_id}/cancel", response_model=schemas.Job)
def cancel_job(job_id: uuid.UUID, settings: SettingsDep, session: SessionDep) -> schemas.Job:
    """Задача сразу получает статус cancelled. Пачка, которую уже считает воркер, досчитывается и записывается;
    следующие пачки видят отмену и пропускают работу.
    """
    cancelled = session.scalar(
        update(models.Job)
        .where(models.Job.id == job_id, models.Job.status.in_(ACTIVE_STATUSES))
        .values(status="cancelled", cancel_requested=True, finished_at=func.now())
        .returning(models.Job.id)
    )
    if cancelled is None:
        status = session.scalar(select(models.Job.status).where(models.Job.id == job_id))
        if status is None:
            raise _job_not_found()
        message = "Задача уже отменена." if status == "cancelled" else "Задача уже завершена, отменить её нельзя."
        raise invalid_state(message, status=status)
    session.commit()
    log.info("Задача отменена", extra={"job_id": str(job_id)})
    return job_views(session, settings, [job_id])[0]


@router.delete("/jobs/{job_id}", status_code=204)
def delete_job(job_id: uuid.UUID, settings: SettingsDep, session: SessionDep) -> Response:
    """Удаляет задачу с позициями, предсказаниями и решениями (каскад в схеме), а файл — если других задач
    по нему нет. Эталонные примеры остаются: у них только обнуляется ссылка на позицию.
    """
    file_id = session.scalar(delete(models.Job).where(models.Job.id == job_id).returning(models.Job.file_id))
    if file_id is None:
        raise _job_not_found()
    storage_path = None
    if not session.scalar(select(exists().where(models.Job.file_id == file_id))):
        storage_path = session.scalar(
            delete(models.SourceFile).where(models.SourceFile.id == file_id).returning(models.SourceFile.storage_path)
        )
    session.commit()
    if storage_path:
        uploads.remove_upload(settings.upload_dir, storage_path)
    log.info("Задача удалена", extra={"job_id": str(job_id)})
    return Response(status_code=204)


def _job_not_found() -> ApiError:
    return not_found("job", "Задача не найдена.")
