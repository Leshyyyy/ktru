"""POST /classify (FR-9.3): несколько строк синхронно через worker-ml, ничего не сохраняет."""

from __future__ import annotations

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.core import models

from .conftest import Stack
from .openapi import assert_matches_schema

pytestmark = pytest.mark.db


def _classify(stack: Stack, *texts: str) -> object:
    return stack.client.post("/api/v1/classify", json={"items": [{"text": t} for t in texts]})


def test_classify_returns_results_in_order(stack: Stack) -> None:
    response = _classify(stack, "Ноутбук Lenovo IdeaPad 3", "Бумага офисная А4", "__fail__ проверка")
    assert response.status_code == 200, response.text
    results = response.json()["results"]
    for result in results:
        assert_matches_schema("ClassifyResult", result)
    assert [r["text"] for r in results] == ["Ноутбук Lenovo IdeaPad 3", "Бумага офисная А4", "__fail__ проверка"]
    assert results[0]["predictions"][0]["ktru_code"].startswith("99.99.")  # коды из версии каталога задачи
    assert (results[2]["predictions"], results[2]["path"]) == ([], None)
    assert results[2]["error"]
    with Session(stack.engine) as session:  # ничего не сохраняется
        assert session.scalar(select(func.count()).select_from(models.Item)) == 0


def test_classify_validation(stack: Stack) -> None:
    for body in ({"items": []}, {}, {"items": [{"text": "x" * 5001}]}):
        response = stack.client.post("/api/v1/classify", json=body)
        assert response.status_code == 400, body
        assert response.json()["error"]["code"] == "invalid_request"
    response = _classify(stack, *(f"Товар {n}" for n in range(21)))
    assert (response.status_code, response.json()["error"]["details"]["max"]) == (400, 20)


def test_classify_when_ml_is_busy(stack: Stack) -> None:
    stack.queue.classify_timeout = True
    response = _classify(stack, "Ноутбук")
    assert response.status_code == 503
    assert_matches_schema("Error", response.json())
    assert response.headers["Retry-After"] == "30"
    stack.queue.classify_timeout, stack.queue.broken = False, True
    assert _classify(stack, "Ноутбук").status_code == 503
