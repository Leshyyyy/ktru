"""Тег jobs: GET /jobs/{id}/export — исходные колонки плюс код, наименование, уверенность, статус, источник
и предупреждения (FR-7.1, FR-7.2).

Файл собирает backend/files (export_job_results): формат, экранирование формул, имя файла по RFC 5987.
Здесь — проверка статуса задачи, итоговые коды из v_item_final и заголовок X-Unreviewed-Count. До 1000 позиций
выгрузка формируется синхронно (замер — в описании PR).
"""

from __future__ import annotations

import logging
import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.core import models
from backend.core.processing import ACTIVE_STATUSES
from backend.core.settings import Settings
from backend.files import UnreviewedItemsError, export_job_results

from ..deps import get_db, get_settings
from ..errors import ApiError, invalid_state, not_found

log = logging.getLogger(__name__)

router = APIRouter(tags=["jobs"])

EXPORTABLE = ("done", "done_with_errors", "cancelled")


@router.get("/jobs/{job_id}/export", response_class=Response)
def export_job(
    job_id: uuid.UUID,
    settings: Annotated[Settings, Depends(get_settings)],
    session: Annotated[Session, Depends(get_db)],
    export_format: Annotated[Literal["source", "xlsx", "csv", "json"], Query(alias="format")] = "source",
    allow_unreviewed: bool = True,
) -> Response:
    found = session.execute(
        select(models.Job, models.SourceFile)
        .join(models.SourceFile, models.SourceFile.id == models.Job.file_id)
        .where(models.Job.id == job_id)
    ).first()
    if found is None:
        raise not_found("job", "Задача не найдена.")
    job, source = found
    if job.status not in EXPORTABLE:
        message = "Задача ещё обрабатывается." if job.status in ACTIVE_STATUSES else "Задача завершилась ошибкой."
        raise invalid_state(message, status=job.status)

    mapping = job.column_mapping
    try:
        result = export_job_results(
            _export_items(session, job_id),
            headers=_headers(source, mapping.get("sheet")),
            export_format=export_format,
            original_filename=source.filename,
            source_path=settings.upload_dir / source.storage_path,
            sheet_name=mapping.get("sheet"),
            allow_unreviewed=allow_unreviewed,
            header_row=mapping.get("header_row", 0),
        )
    except UnreviewedItemsError as e:
        details = e.details if isinstance(e.details, dict) else None
        raise ApiError(409, "unreviewed_items", e.message, details) from None
    log.info("Результат выгружен", extra={"job_id": str(job_id), "format": export_format, "bytes": len(result.content)})
    return Response(
        content=result.content,
        media_type=result.media_type,
        headers={"Content-Disposition": result.content_disposition, "X-Unreviewed-Count": str(result.unreviewed_count)},
    )


def _export_items(session: Session, job_id: uuid.UUID) -> list[dict[str, Any]]:
    final = models.v_item_final
    rows = session.execute(
        select(
            models.Item.id,
            models.Item.row_index,
            models.Item.raw_row,
            models.Item.status,
            models.Item.flags,
            models.Item.error,
            final.c.ktru_code,
            final.c.name,
            final.c.source,
            final.c.confidence,
        )
        .join(final, final.c.item_id == models.Item.id)
        .where(models.Item.job_id == job_id)
        .order_by(models.Item.row_index)
    ).all()
    return [
        {
            "item_id": row.id,
            "row_index": row.row_index,
            "raw_row": row.raw_row,
            "ktru_code": row.ktru_code,
            "ktru_name": row.name,
            # Из колонки real приходит 0.9399999976 — в json это попало бы как есть.
            "confidence": round(row.confidence, 4) if row.confidence is not None else None,
            "status": row.status,
            "source": row.source if row.ktru_code else None,
            "flags": row.flags,
            "error": row.error,
        }
        for row in rows
    ]


def _headers(source: models.SourceFile, sheet_name: str | None) -> list[str]:
    """Заголовки колонок, какими их показал POST /files: для xlsx, csv и json, собираемых с нуля."""
    for sheet in source.parsed_meta.get("sheets", []):
        if sheet_name is None or sheet["name"] == sheet_name:
            return [column["header"] for column in sheet["columns"]]
    return []
