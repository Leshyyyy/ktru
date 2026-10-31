"""Тег files: POST /files — загрузка спецификации и предпросмотр для сопоставления колонок (FR-1.1–FR-1.5).

Сам разбор делает backend/files (read_file); здесь — проверка формата и размера, хранение, повтор файла
(duplicate_of_job_id) и запомненная раскладка колонок (layout_hint).
"""

from __future__ import annotations

import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.core import models, uploads
from backend.core.settings import Settings
from backend.files import FileInfo, FileParsingError, LayoutHint, read_file
from backend.files.hash import compute_headers_hash

from ..deps import get_db, get_settings
from ..errors import ApiError, file_error, file_too_large

log = logging.getLogger(__name__)

router = APIRouter(tags=["files"])


@router.post("/files", status_code=201, response_model=FileInfo)
def upload_file(
    file: UploadFile,
    settings: Annotated[Settings, Depends(get_settings)],
    session: Annotated[Session, Depends(get_db)],
) -> FileInfo:
    filename = uploads.clean_filename(file.filename)
    ext = uploads.extension(filename)
    if ext not in settings.upload_extensions:
        raise ApiError(415, "unsupported_file_type", supported_formats_message(settings.upload_extensions))

    file_id = uuid.uuid4()
    try:
        stored = uploads.save_upload(file.file, settings.upload_dir, file_id, ext, settings.max_file_mb * 1024 * 1024)
    except uploads.UploadTooLarge:
        raise file_too_large(settings.max_file_mb) from None

    try:
        info = read_file(settings.upload_dir / stored.storage_path, original_filename=filename, file_id=file_id)
        info.duplicate_of_job_id = _duplicate_of(session, stored.sha256)
        info.layout_hint = _layout_hint(session, info)
        session.add(
            models.SourceFile(
                id=file_id,
                filename=filename,
                mime_type=uploads.MIME_TYPES[ext],
                size_bytes=stored.size_bytes,
                sha256=stored.sha256,
                storage_path=stored.storage_path,
                parsed_meta={"sheets": [sheet.model_dump(mode="json") for sheet in info.sheets]},
            )
        )
        session.commit()
    except FileParsingError as e:
        uploads.remove_upload(settings.upload_dir, stored.storage_path)
        log.info("Файл не принят", extra={"code": e.code, "reason": str(e.__cause__ or e.message)})
        raise file_error(*uploads.file_error(e)) from None
    except BaseException:
        uploads.remove_upload(settings.upload_dir, stored.storage_path)
        raise
    log.info("Файл загружен", extra={"file_id": str(file_id), "size_bytes": stored.size_bytes, "format": ext})
    return info


def supported_formats_message(extensions: list[str]) -> str:
    names = ", ".join(extensions[:-1]) + f" и {extensions[-1]}" if len(extensions) > 1 else extensions[0]
    return f"Поддерживаются файлы {names}."


def _duplicate_of(session: Session, sha256: str) -> uuid.UUID | None:
    """Последняя задача по файлу с тем же содержимым; упавшие не предлагаем — открывать в них нечего."""
    return session.scalar(
        select(models.Job.id)
        .join(models.SourceFile, models.SourceFile.id == models.Job.file_id)
        .where(models.SourceFile.sha256 == sha256, models.Job.status != "failed")
        .order_by(models.Job.created_at.desc())
        .limit(1)
    )


def _layout_hint(session: Session, info: FileInfo) -> LayoutHint | None:
    """Сопоставление, подтверждённое для таких же заголовков; ищем по листам по порядку, как интерфейс."""
    hashes = [compute_headers_hash([column.header for column in sheet.columns]) for sheet in info.sheets]
    layouts = {
        layout.header_hash: layout
        for layout in session.scalars(select(models.FileLayout).where(models.FileLayout.header_hash.in_(hashes)))
    }
    for header_hash in hashes:
        if layout := layouts.get(header_hash):
            return LayoutHint.model_validate({"header_row": layout.header_row, "column_mapping": layout.column_mapping})
    return None
