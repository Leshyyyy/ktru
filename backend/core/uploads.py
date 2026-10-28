"""Загруженные спецификации: хранилище на томе uploads (api пишет, worker-io только читает) и ошибки разбора.

Файл лежит под именем <file_id>.<расширение>: имя от пользователя не участвует в пути, а расширение нужно
backend/files — формат он определяет по нему. В source_files.storage_path пишется имя относительно UPLOAD_DIR.
"""

from __future__ import annotations

import hashlib
import logging
import os
import unicodedata
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO

from backend.files import FileParsingError

log = logging.getLogger(__name__)

UNREADABLE_MESSAGE = "Не удалось прочитать файл. Проверьте, что он не повреждён и не защищён паролем."
_FILE_ERROR_CODES = ("file_too_large", "unsupported_file_type", "file_unreadable", "mapping_invalid", "limit_exceeded")

MIME_TYPES = {
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "xls": "application/vnd.ms-excel",
    "csv": "text/csv",
    "pdf": "application/pdf",
}
_CHUNK = 1 << 20
_MAX_FILENAME = 255


class UploadTooLarge(Exception):
    """Файл больше лимита: записанная часть уже удалена."""


@dataclass(frozen=True)
class StoredFile:
    storage_path: str  # относительно UPLOAD_DIR
    size_bytes: int
    sha256: str


def clean_filename(raw: str | None) -> str:
    """Имя для показа и выгрузки: без пути (браузеры шлют C:\\fakepath\\…) и управляющих символов, до 255 знаков."""
    name = (raw or "").replace("\\", "/").rsplit("/", 1)[-1]
    name = "".join(ch for ch in name if unicodedata.category(ch)[0] != "C").strip()
    if len(name) > _MAX_FILENAME:
        stem, dot, ext = name.rpartition(".")
        name = stem[: _MAX_FILENAME - len(ext) - 1] + dot + ext if dot and len(ext) < 10 else name[:_MAX_FILENAME]
    return name


def extension(filename: str) -> str:
    """Расширение в нижнем регистре без точки; пустая строка, если его нет."""
    stem, dot, ext = filename.rpartition(".")
    return ext.lower() if dot and stem else ""


def save_upload(src: BinaryIO, upload_dir: Path, file_id: uuid.UUID, ext: str, max_bytes: int) -> StoredFile:
    """Копирует поток в UPLOAD_DIR, считая размер и sha256. Файл появляется под своим именем только целиком."""
    upload_dir.mkdir(parents=True, exist_ok=True)
    name = f"{file_id}.{ext}"
    partial = upload_dir / f".{name}.part"
    digest = hashlib.sha256()
    size = 0
    try:
        with partial.open("xb") as dst:
            while chunk := src.read(_CHUNK):
                size += len(chunk)
                if size > max_bytes:
                    raise UploadTooLarge
                digest.update(chunk)
                dst.write(chunk)
        os.replace(partial, upload_dir / name)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    return StoredFile(storage_path=name, size_bytes=size, sha256=digest.hexdigest())


def file_error(exc: FileParsingError) -> tuple[str, str, dict[str, Any] | None]:
    """Ошибка backend/files → (код из openapi.yaml, текст для человека, details)."""
    code = exc.code if exc.code in _FILE_ERROR_CODES else "file_unreadable"
    # Ошибки библиотек (calamine, pdfplumber) backend/files оборачивает через `from e`: их текст — внутренности.
    message = UNREADABLE_MESSAGE if exc.__cause__ is not None or not exc.message else exc.message
    details = exc.details if isinstance(exc.details, dict) else {}
    return code, message, {k: v for k, v in details.items() if k != "error_code"} or None


def remove_upload(upload_dir: Path, storage_path: str) -> None:
    """Удаляет файл из хранилища; ошибка удаления только пишется в лог — запись в базе уже удалена."""
    try:
        (upload_dir / storage_path).unlink(missing_ok=True)
    except OSError:
        log.warning("Не удалось удалить загруженный файл", extra={"storage_path": storage_path}, exc_info=True)
