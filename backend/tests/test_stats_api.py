"""GET /stats (FR-9.2): счётчики, green_correction_rate, скорость, доля ошибок, последние ошибки."""

from __future__ import annotations

from typing import Any

import pytest

from .conftest import Stack
from .openapi import assert_matches_schema

pytestmark = pytest.mark.db

ROWS = [[f"Товар номер {n}", "", 1, "шт"] for n in range(12)] + [["Позиция __fail__ для проверки", "", 1, "шт"]]


def _stats(stack: Stack) -> dict[str, Any]:
    response = stack.client.get("/api/v1/stats")
    assert response.status_code == 200, response.text
    assert_matches_schema("Stats", response.json())
    return response.json()


def test_stats_without_data(stack: Stack) -> None:
    assert _stats(stack) == {
        "jobs_total": 0,
        "items_total": 0,
        "green_correction_rate": None,
        "avg_seconds_per_1000": None,
        "failed_items_share": 0.0,
        "recent_errors": [],
    }


def test_stats_after_review(stack: Stack) -> None:
    job = stack.finished_job(ROWS)
    items = stack.client.get(f"/api/v1/jobs/{job['job_id']}/items", params={"page_size": 200}).json()["items"]
    green = [i for i in items if i["band"] == "green"]
    other = [i for i in items if i["band"] in ("yellow", "red")]
    assert len(green) >= 2, "заглушка дала мало зелёных позиций — поменяйте строки"
    assert other, "заглушка не дала ни одной жёлтой или красной позиции — поменяйте строки"

    def correct(item: dict[str, Any]) -> None:
        code = next(c for c in ("99.99.05.000-00000001", "99.99.06.000-00000001") if c != item["final"]["ktru_code"])
        response = stack.client.put(f"/api/v1/items/{item['item_id']}/decision", json={"ktru_code": code})
        assert response.status_code == 200, response.text

    stack.client.post(f"/api/v1/items/{green[0]['item_id']}/confirm")  # зелёная, проверена человеком
    correct(green[1])  # зелёная, исправлена
    correct(other[0])  # не зелёная — в метрику не входит
    stack.client.post(f"/api/v1/jobs/{job['job_id']}/confirm-bulk", json={"min_confidence": 0})  # bulk не входит

    stats = _stats(stack)
    assert (stats["jobs_total"], stats["items_total"]) == (1, 13)
    assert stats["green_correction_rate"] == 0.5
    assert stats["failed_items_share"] == round(1 / 13, 4)
    assert stats["avg_seconds_per_1000"] is not None
    [error] = stats["recent_errors"]
    assert error["job_id"] == job["job_id"]
