"""Комплект: атомарное создание, обычные задачи, общая сводка и выгрузка."""

from __future__ import annotations

import csv
import io
import json
import uuid
from typing import Any

import openpyxl
import pytest
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from backend.core import models

from .conftest import HEADER, MAPPING, ROWS, Stack, xlsx_bytes
from .openapi import assert_matches_schema

pytestmark = pytest.mark.db


def _job(file_id: str, **changes: Any) -> dict[str, Any]:
    return {"file_id": file_id, "column_mapping": MAPPING, **changes}


def _batch(stack: Stack, jobs: list[dict[str, Any]], name: str | None = None) -> dict[str, Any]:
    response = stack.client.post("/api/v1/batches", json={"name": name, "jobs": jobs})
    assert response.status_code == 202, response.text
    assert_matches_schema("Batch", response.json())
    return response.json()


def _error(response: Any, status: int, code: str) -> dict[str, Any]:
    assert response.status_code == status, response.text
    body = response.json()
    assert_matches_schema("Error", body)
    assert body["error"]["code"] == code
    return body["error"]


def test_batch_creates_normal_jobs_and_lists_them_in_order(stack: Stack) -> None:
    first = stack.upload(xlsx_bytes([HEADER, ROWS[0]]), "first.xlsx")
    second = stack.upload(xlsx_bytes([HEADER, ROWS[1]]), "second.xlsx")
    created = _batch(stack, [_job(first["file_id"]), _job(second["file_id"])], "Офис")
    batch_id = created["batch_id"]
    assert (created["name"], created["status"], created["progress"]) == ("Офис", "processing", 0)
    jobs = created["jobs"]
    assert [job["filename"] for job in jobs] == ["first.xlsx", "second.xlsx"]
    assert all(job["batch_id"] == batch_id for job in jobs)
    assert stack.queue.parse == [job["job_id"] for job in jobs]
    page = stack.client.get("/api/v1/batches", params={"page_size": 1}).json()
    assert (page["total"], page["page"], page["page_size"]) == (1, 1, 1)
    assert_matches_schema("Batch", page["items"][0])
    filtered = stack.client.get("/api/v1/jobs", params={"batch_id": batch_id, "page_size": 100}).json()
    assert (filtered["total"], [job["job_id"] for job in filtered["items"]]) == (
        2,
        [job["job_id"] for job in jobs[::-1]],
    )
    assert stack.client.get("/api/v1/jobs", params={"batch_id": str(uuid.uuid4())}).json()["total"] == 0
    assert stack.client.get("/api/v1/config").json()["limits"]["max_files_per_batch"] == 10


def test_batch_creation_is_atomic_and_reports_job_index(stack: Stack) -> None:
    first = stack.upload(xlsx_bytes([HEADER, ROWS[0]]), "first.xlsx")
    second = stack.upload(xlsx_bytes([HEADER, ROWS[1]]), "second.xlsx")
    bad = _job(second["file_id"], column_mapping=[{"index": 1, "role": "unit"}])
    error = _error(
        stack.client.post("/api/v1/batches", json={"jobs": [_job(first["file_id"]), bad]}), 422, "mapping_invalid"
    )
    assert error["details"]["job_index"] == 1
    assert stack.queue.parse == []
    with Session(stack.engine) as session:
        assert session.scalar(select(func.count()).select_from(models.Batch)) == 0
        assert session.scalar(select(func.count()).select_from(models.Job)) == 0

    missing = _error(
        stack.client.post("/api/v1/batches", json={"jobs": [_job(first["file_id"]), _job(str(uuid.uuid4()))]}),
        404,
        "not_found",
    )
    assert missing["details"]["job_index"] == 1


def test_batch_limit_and_validation(stack: Stack) -> None:
    file_id = stack.upload(xlsx_bytes([HEADER, ROWS[0]]))["file_id"]
    error = _error(stack.client.post("/api/v1/batches", json={"jobs": [_job(file_id)] * 11}), 422, "limit_exceeded")
    assert error["details"]["max_files_per_batch"] == 10
    _error(stack.client.post("/api/v1/batches", json={"jobs": []}), 400, "invalid_request")
    _error(stack.client.get(f"/api/v1/batches/{uuid.uuid4()}"), 404, "not_found")
    assert stack.queue.parse == []


def test_batch_summary_status_and_removed_job(stack: Stack) -> None:
    first = stack.upload(xlsx_bytes([HEADER, ROWS[0]]), "first.xlsx")
    second = stack.upload(xlsx_bytes([HEADER, ROWS[1]]), "second.xlsx")
    created = _batch(stack, [_job(first["file_id"]), _job(second["file_id"])])
    batch_id = created["batch_id"]
    stack.drain()
    done = stack.client.get(f"/api/v1/batches/{batch_id}").json()
    assert_matches_schema("Batch", done)
    assert (done["status"], done["total_items"], done["processed_items"], done["progress"]) == (
        "done",
        2,
        2,
        1.0,
    )
    for field in done["summary"]:
        assert done["summary"][field] == sum(job["summary"][field] for job in done["jobs"])

    ids = [uuid.UUID(job["job_id"]) for job in done["jobs"]]
    with Session(stack.engine) as session:
        session.execute(update(models.Job).where(models.Job.id == ids[1]).values(status="failed"))
        session.commit()
    assert stack.client.get(f"/api/v1/batches/{batch_id}").json()["status"] == "done_with_errors"
    with Session(stack.engine) as session:
        session.execute(update(models.Job).where(models.Job.id == ids[0]).values(status="failed"))
        session.commit()
    assert stack.client.get(f"/api/v1/batches/{batch_id}").json()["status"] == "failed"
    assert stack.client.delete(f"/api/v1/jobs/{ids[1]}").status_code == 204
    after = stack.client.get(f"/api/v1/batches/{batch_id}").json()
    assert len(after["jobs"]) == 1
    assert after["total_items"] == 1


def test_batch_summary_during_processing_and_cancellation(stack: Stack) -> None:
    first = stack.upload(xlsx_bytes([HEADER, ROWS[0]]), "first.xlsx")
    second = stack.upload(xlsx_bytes([HEADER, ROWS[1]]), "second.xlsx")
    created = _batch(stack, [_job(first["file_id"]), _job(second["file_id"])])
    batch_id = created["batch_id"]
    stack.parse_only(created["jobs"][0]["job_id"])
    middle = stack.client.get(f"/api/v1/batches/{batch_id}").json()
    assert (middle["status"], middle["total_items"], middle["processed_items"]) == ("processing", 1, 0)
    assert middle["summary"]["missing_required"] == 0

    for job in created["jobs"]:
        cancelled = stack.client.post(f"/api/v1/jobs/{job['job_id']}/cancel")
        assert cancelled.status_code == 200, cancelled.text
    final = stack.client.get(f"/api/v1/batches/{batch_id}").json()
    assert (final["status"], len(final["jobs"]), final["total_items"]) == ("cancelled", 2, 1)
    assert stack.client.get(f"/api/v1/batches/{batch_id}/export").status_code == 200


def test_batch_export_in_four_formats_and_guards(stack: Stack) -> None:
    first = stack.upload(xlsx_bytes([HEADER, ROWS[0]]), "first.xlsx")
    second = stack.upload(xlsx_bytes([HEADER, ROWS[1]]), "second.xlsx")
    batch = _batch(stack, [_job(first["file_id"]), _job(second["file_id"])])
    url = f"/api/v1/batches/{batch['batch_id']}/export"
    _error(stack.client.get(url), 409, "invalid_state")
    _error(stack.client.get(url, params={"format": "source"}), 400, "invalid_request")
    stack.drain()
    _error(stack.client.get(url, params={"allow_unreviewed": "false"}), 409, "unreviewed_items")

    xlsx = stack.client.get(url)
    missing_required = stack.client.get(f"/api/v1/batches/{batch['batch_id']}").json()["summary"]["missing_required"]
    assert (xlsx.status_code, xlsx.headers["x-unreviewed-count"], xlsx.headers["x-missing-required-count"]) == (
        200,
        "2",
        str(missing_required),
    )
    assert "attachment" in xlsx.headers["content-disposition"]
    sheet = openpyxl.load_workbook(io.BytesIO(xlsx.content)).active
    assert [sheet.cell(1, column).value for column in (1, 2)] == ["Файл-источник", "Исходный текст"]
    assert [sheet.cell(row, 1).value for row in (2, 3)] == ["first.xlsx", "second.xlsx"]

    csv_response = stack.client.get(url, params={"format": "csv"})
    csv_rows = list(csv.reader(io.StringIO(csv_response.content.decode("utf-8-sig")), delimiter=";"))
    assert csv_rows[0][:2] == ["Файл-источник", "Исходный текст"]
    assert [row[0] for row in csv_rows[1:]] == ["first.xlsx", "second.xlsx"]
    json_response = stack.client.get(url, params={"format": "json"})
    assert [row["source_filename"] for row in json.loads(json_response.content)] == ["first.xlsx", "second.xlsx"]
    application = stack.client.get(url, params={"format": "application"})
    assert application.status_code == 200
    assert openpyxl.load_workbook(io.BytesIO(application.content)).active.cell(1, 1).value == "Файл-источник"
