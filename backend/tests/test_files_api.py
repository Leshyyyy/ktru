"""POST /files: формат и размер, хранение файла, предпросмотр из backend/files, duplicate_of_job_id и layout_hint."""

from __future__ import annotations

import hashlib
import os
import time
import uuid
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from backend.app.main import create_app
from backend.core import models, uploads
from backend.core.settings import Settings

from .conftest import HEADER, ROWS, Stack, xlsx_bytes
from .openapi import assert_matches_schema


@pytest.fixture
def client(settings: Settings, tmp_path: Path) -> Iterator[tuple[TestClient, Path]]:
    """API без базы: отказы POST /files случаются раньше, чем запрос к ней."""
    upload_dir = tmp_path / "uploads"
    app = create_app(settings.model_copy(update={"upload_dir": upload_dir, "max_file_mb": 1}))
    with TestClient(app) as c:
        yield c, upload_dir


def _error(response: Any, status: int, code: str) -> dict[str, Any]:
    assert response.status_code == status, response.text
    body = response.json()
    assert_matches_schema("Error", body)
    assert body["error"]["code"] == code
    return body["error"]


def _left_files(upload_dir: Path) -> list[Path]:
    return list(upload_dir.iterdir()) if upload_dir.exists() else []


@pytest.mark.parametrize("filename", ["spec.docx", "spec.xls", "spec", ".xlsx"])
def test_unsupported_format_is_415(client: tuple[TestClient, Path], filename: str) -> None:
    c, upload_dir = client
    error = _error(c.post("/api/v1/files", files={"file": (filename, b"data")}), 415, "unsupported_file_type")
    assert error["message"] == "Поддерживаются файлы xlsx, csv и pdf."
    assert _left_files(upload_dir) == []


def test_missing_file_part_is_400(client: tuple[TestClient, Path]) -> None:
    c, _ = client
    error = _error(c.post("/api/v1/files", data={"name": "x"}), 400, "invalid_request")
    assert error["message"] == "Некорректный запрос: поле file обязательно."
    # Часть без имени файла — это не файл.
    error = _error(c.post("/api/v1/files", files={"file": ("", b"data")}), 400, "invalid_request")
    assert error["message"] == "Некорректный запрос: в поле file нужно передать файл."


def test_file_over_limit_is_413_and_not_kept(client: tuple[TestClient, Path]) -> None:
    c, upload_dir = client
    error = _error(c.post("/api/v1/files", files={"file": ("big.csv", b"a;b\n" * 300_000)}), 413, "file_too_large")
    assert error["message"] == "Файл больше 1 МБ. Разбейте его на части."
    assert _left_files(upload_dir) == []


def test_broken_xlsx_is_422_without_library_details(client: tuple[TestClient, Path]) -> None:
    c, upload_dir = client
    error = _error(c.post("/api/v1/files", files={"file": ("spec.xlsx", b"not a zip")}), 422, "file_unreadable")
    assert error["message"] == "Не удалось прочитать файл. Проверьте, что он не повреждён и не защищён паролем."
    assert _left_files(upload_dir) == []


@pytest.mark.db
def test_upload_stores_file_and_returns_preview(stack: Stack) -> None:
    content = xlsx_bytes([HEADER, *ROWS])
    info = stack.upload(content, filename="C:\\fakepath\\Спецификация №1.xlsx")
    assert_matches_schema("FileInfo", info)
    assert info["filename"] == "Спецификация №1.xlsx"
    assert info["size_bytes"] == len(content)
    assert (info["duplicate_of_job_id"], info["layout_hint"]) == (None, None)
    sheet = info["sheets"][0]
    assert (sheet["name"], sheet["row_count"], sheet["header_row"]) == ("Лист1", 3, 0)
    assert [c["suggested_role"] for c in sheet["columns"]] == ["name", "characteristics", "quantity", "unit"]
    assert sheet["preview"][0][0] == ROWS[0][0]

    with Session(stack.engine) as session:
        stored = session.scalars(select(models.SourceFile)).one()
    assert str(stored.id) == info["file_id"]
    assert stored.sha256 == hashlib.sha256(content).hexdigest()
    assert stored.mime_type == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    assert stored.parsed_meta["sheets"][0]["columns"][0]["header"] == HEADER[0]
    assert stack.stored_files() == [stack.settings.upload_dir / f"{info['file_id']}.xlsx"]


@pytest.mark.db
def test_csv_upload(stack: Stack) -> None:
    content = "Наименование;Кол-во\nПерчатки медицинские;100\n".encode("cp1251")
    info = stack.upload(content, filename="spec.csv")
    assert info["sheets"][0]["preview"] == [["Перчатки медицинские", "100"]]


@pytest.mark.db
def test_repeated_file_and_known_headers(stack: Stack) -> None:
    content = xlsx_bytes([HEADER, *ROWS])
    first = stack.upload(content)
    mapping = [{"index": 0, "role": "name"}, {"index": 1, "role": "description"}]
    job = stack.create_job(first["file_id"], mapping=mapping)

    again = stack.upload(content)
    assert again["duplicate_of_job_id"] == job["job_id"]
    assert again["layout_hint"] == {"header_row": 0, "column_mapping": mapping}

    # Другой файл с теми же заголовками: не повтор, но сопоставление подставляется.
    other = stack.upload(xlsx_bytes([HEADER, ROWS[0]]))
    assert other["duplicate_of_job_id"] is None
    assert other["layout_hint"] == {"header_row": 0, "column_mapping": mapping}

    # Упавшую задачу как повтор не предлагаем: открывать в ней нечего.
    with Session(stack.engine) as session, session.begin():
        session.get(models.Job, job["job_id"]).status = "failed"
    assert stack.upload(content)["duplicate_of_job_id"] is None


@pytest.mark.db
def test_cleanup_removes_old_uploads_without_job(stack: Stack) -> None:
    """io.cleanup_uploads: старые файлы без задачи и обрывки — удаляются; файлы задач и свежие — остаются."""
    old = stack.upload(xlsx_bytes([HEADER, *ROWS]))["file_id"]
    fresh = stack.upload(xlsx_bytes([HEADER, *ROWS[:1]]))["file_id"]
    with_job = stack.upload(xlsx_bytes([HEADER, *ROWS[:2]]))["file_id"]
    stack.create_job(with_job)
    with Session(stack.engine) as session, session.begin():
        session.execute(
            update(models.SourceFile)
            .where(models.SourceFile.id.in_([uuid.UUID(old), uuid.UUID(with_job)]))
            .values(created_at=func.now() - timedelta(days=2))
        )
    upload_dir = stack.settings.upload_dir
    stray, partial = upload_dir / f"{uuid.uuid4()}.xlsx", upload_dir / ".x.part"
    for path in (stray, partial):
        path.write_bytes(b"x")
        os.utime(path, (time.time() - 3 * 86400,) * 2)

    assert uploads.cleanup_uploads(stack.engine, upload_dir, timedelta(hours=24)) == 3
    with Session(stack.engine) as session:
        left = set(session.scalars(select(models.SourceFile.id)).all())
    assert left == {uuid.UUID(fresh), uuid.UUID(with_job)}
    assert sorted(p.name.split(".")[0] for p in stack.stored_files()) == sorted([fresh, with_job])
    assert uploads.cleanup_uploads(stack.engine, upload_dir, timedelta(hours=24)) == 0
