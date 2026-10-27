"""Задачи и позиции через API на настоящей базе: POST/GET /jobs, отмена, удаление, GET /jobs/{id}/items, /items/{id}.

Очередь — в памяти (FakeQueue), задачи воркеров выполняет drain() с StubPipeline: поток тот же, что в compose.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.core import models
from backend.core.readiness import READY_KEY

from .conftest import HEADER, MAPPING, ROWS, Stack, xlsx_bytes
from .openapi import assert_matches_schema

pytestmark = pytest.mark.db

FAIL_ROW = ["Позиция __fail__ для проверки ошибки", "", 1, "шт"]


def _items(stack: Stack, job_id: str, **params: Any) -> dict[str, Any]:
    response = stack.client.get(f"/api/v1/jobs/{job_id}/items", params=params)
    assert response.status_code == 200, response.text
    body = response.json()
    assert_matches_schema("ItemList", body)
    return body


def _error(response: Any, status: int, code: str) -> dict[str, Any]:
    assert response.status_code == status, response.text
    body = response.json()
    assert_matches_schema("Error", body)
    assert body["error"]["code"] == code
    return body["error"]


def test_job_is_processed_end_to_end(stack: Stack) -> None:
    info = stack.upload(xlsx_bytes([HEADER, *ROWS, FAIL_ROW]))
    created = stack.create_job(info["file_id"], sheet="Лист1", header_row=0, options={"use_llm": False})
    assert_matches_schema("Job", created)
    assert created["status"] == "queued"
    assert (created["total_items"], created["progress"], created["eta_seconds"]) == (0, 0.0, None)
    assert created["filename"] == "spec.xlsx"
    assert stack.queue.parse == [created["job_id"]]

    stack.drain()

    job = stack.client.get(f"/api/v1/jobs/{created['job_id']}").json()
    assert_matches_schema("Job", job)
    assert job["status"] == "done_with_errors"
    assert (job["total_items"], job["processed_items"], job["failed_items"], job["progress"]) == (4, 3, 1, 1.0)
    assert job["finished_at"] is not None
    assert job["started_at"] is not None
    summary = job["summary"]
    assert summary["green"] + summary["yellow"] + summary["red"] == 3
    assert (summary["confirmed"], summary["corrected"]) == (0, 0)

    items = _items(stack, job["job_id"])["items"]
    assert [i["row_index"] for i in items] == [0, 1, 2, 3]
    assert items[0]["raw_text"] == 'Ноутбук Lenovo IdeaPad 3 15IAU7 15.6", Core i5, 16 ГБ'
    for item in items[:3]:
        top = item["predictions"][0]
        assert item["status"] == ("processed" if item["band"] == "green" else "needs_review")
        assert item["final"] == {
            "ktru_code": top["ktru_code"],
            "name": top["name"],
            "source": "prediction",
            "confidence": top["confidence"],
        }
        assert top["name"]
        assert top["okpd2_code"]
        assert top["why_not_chosen"] is None
        assert item["extracted"]["method"] == "rules"
    failed = items[3]
    assert (failed["status"], failed["predictions"], failed["final"], failed["band"]) == ("failed", [], None, None)
    assert failed["error"]

    card = stack.client.get(f"/api/v1/items/{items[0]['item_id']}").json()
    assert_matches_schema("Item", card)
    assert card == items[0]


def test_items_filters_sort_groups_and_pages(stack: Stack) -> None:
    twin = ["Монитор Samsung S24R350", "24 дюйма", 2, "шт"]  # два одинаковых — группа повторов
    job = stack.finished_job([*ROWS, twin, twin, FAIL_ROW])
    job_id, summary = job["job_id"], job["summary"]

    for band in ("green", "yellow", "red"):
        assert _items(stack, job_id, band=band)["total"] == summary[band]
    assert _items(stack, job_id, status="failed")["total"] == 1
    assert [i["row_index"] for i in _items(stack, job_id, q="монитор SAMSUNG")["items"]] == [3, 4]
    assert _items(stack, job_id, q="100%_")["total"] == 0  # % и _ в запросе — обычные символы

    group = [i for i in _items(stack, job_id)["items"] if i["group_id"]]
    assert len(group) == 2
    assert group[0]["group_id"] == group[1]["group_id"]
    assert {i["group_size"] for i in group} == {2}
    assert _items(stack, job_id, group_id=group[0]["group_id"])["total"] == 2

    by_confidence = _items(stack, job_id, sort="confidence_asc")["items"]
    assert by_confidence[0]["status"] == "failed"  # без предсказаний — сверху
    confidences = [i["predictions"][0]["confidence"] for i in by_confidence[1:]]
    assert confidences == sorted(confidences)

    page = _items(stack, job_id, page=2, page_size=2)
    assert (page["total"], page["page"], page["page_size"]) == (6, 2, 2)
    assert [i["row_index"] for i in page["items"]] == [2, 3]

    _error(stack.client.get(f"/api/v1/jobs/{job_id}/items", params={"band": "blue"}), 400, "invalid_request")
    _error(stack.client.get(f"/api/v1/jobs/{job_id}/items", params={"page_size": 500}), 400, "invalid_request")
    _error(stack.client.get(f"/api/v1/jobs/{uuid.uuid4()}/items"), 404, "not_found")


def test_item_card_errors(stack: Stack) -> None:
    error = _error(stack.client.get(f"/api/v1/items/{uuid.uuid4()}"), 404, "not_found")
    assert error["details"] == {"object": "item"}
    _error(stack.client.get("/api/v1/items/not-a-uuid"), 400, "invalid_request")


@pytest.mark.parametrize(
    ("change", "status", "code"),
    [
        ({"sheet": "Нет такого"}, 422, "mapping_invalid"),
        ({"column_mapping": [{"index": 0, "role": "name"}, {"index": 1, "role": "name"}]}, 422, "mapping_invalid"),
        ({"column_mapping": [{"index": 1, "role": "description"}]}, 422, "mapping_invalid"),
        ({"column_mapping": [{"index": 0, "role": "name"}, {"index": 9, "role": "unit"}]}, 422, "mapping_invalid"),
        ({"column_mapping": [{"index": 0, "role": "name"}, {"index": 0, "role": "unit"}]}, 422, "mapping_invalid"),
        ({"column_mapping": []}, 400, "invalid_request"),
        ({"column_mapping": [{"index": 0, "role": "brand"}]}, 400, "invalid_request"),
        ({"header_row": -1}, 400, "invalid_request"),
        ({"file_id": str(uuid.uuid4())}, 404, "not_found"),
    ],
)
def test_create_job_rejects_bad_requests(stack: Stack, change: dict[str, Any], status: int, code: str) -> None:
    info = stack.upload(xlsx_bytes([HEADER, *ROWS]))
    body = {"file_id": info["file_id"], "column_mapping": MAPPING, **change}
    error = _error(stack.client.post("/api/v1/jobs", json=body), status, code)
    assert error["message"]
    assert stack.queue.parse == []


def test_create_job_waits_for_ready_system(stack: Stack) -> None:
    info = stack.upload(xlsx_bytes([HEADER, *ROWS]))
    stack.redis.data.pop(READY_KEY)  # worker-ml не запущен
    response = stack.client.post("/api/v1/jobs", json={"file_id": info["file_id"], "column_mapping": MAPPING})
    _error(response, 503, "not_ready")
    assert response.headers["Retry-After"] == "30"


def test_job_is_created_even_if_broker_is_down(stack: Stack) -> None:
    info = stack.upload(xlsx_bytes([HEADER, *ROWS]))
    stack.queue.broken = True
    job = stack.create_job(info["file_id"])
    assert job["status"] == "queued"  # разбор поставит в очередь watchdog


def test_list_jobs_newest_first(stack: Stack) -> None:
    info = stack.upload(xlsx_bytes([HEADER, *ROWS]))
    ids = [stack.create_job(info["file_id"])["job_id"] for _ in range(3)]
    first = stack.client.get("/api/v1/jobs", params={"page_size": 2}).json()
    assert_matches_schema("Job", first["items"][0])
    assert (first["total"], [j["job_id"] for j in first["items"]]) == (3, ids[:0:-1])
    second = stack.client.get("/api/v1/jobs", params={"page": 2, "page_size": 2}).json()
    assert [j["job_id"] for j in second["items"]] == ids[:1]


def test_cancel_job(stack: Stack) -> None:
    info = stack.upload(xlsx_bytes([HEADER, *ROWS]))
    job = stack.create_job(info["file_id"])
    response = stack.client.post(f"/api/v1/jobs/{job['job_id']}/cancel")
    assert response.status_code == 200
    cancelled = response.json()
    assert_matches_schema("Job", cancelled)
    assert cancelled["status"] == "cancelled"
    assert cancelled["finished_at"] is not None

    stack.drain()  # разбор видит отмену и ничего не создаёт
    assert _items(stack, job["job_id"])["total"] == 0
    error = _error(stack.client.post(f"/api/v1/jobs/{job['job_id']}/cancel"), 409, "invalid_state")
    assert error["details"] == {"status": "cancelled"}

    done = stack.finished_job(ROWS)
    error = _error(stack.client.post(f"/api/v1/jobs/{done['job_id']}/cancel"), 409, "invalid_state")
    assert error["message"] == "Задача уже завершена, отменить её нельзя."
    _error(stack.client.post(f"/api/v1/jobs/{uuid.uuid4()}/cancel"), 404, "not_found")


def test_delete_job_removes_data_and_unused_file(stack: Stack) -> None:
    info = stack.upload(xlsx_bytes([HEADER, *ROWS]))
    first, second = stack.create_job(info["file_id"]), stack.create_job(info["file_id"])
    stack.drain()
    assert len(stack.stored_files()) == 1

    assert stack.client.delete(f"/api/v1/jobs/{first['job_id']}").status_code == 204
    _error(stack.client.get(f"/api/v1/jobs/{first['job_id']}"), 404, "not_found")
    assert len(stack.stored_files()) == 1  # файлом пользуется вторая задача

    assert stack.client.delete(f"/api/v1/jobs/{second['job_id']}").status_code == 204
    assert stack.stored_files() == []
    with Session(stack.engine) as session:
        for table in (models.SourceFile, models.Job, models.Item, models.Prediction):
            assert session.scalar(select(func.count()).select_from(table)) == 0
    _error(stack.client.delete(f"/api/v1/jobs/{second['job_id']}"), 404, "not_found")
