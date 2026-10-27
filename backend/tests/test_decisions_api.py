"""Решения по позициям (FR-6.1–FR-6.4): подтверждение, ручная правка, массовое подтверждение, эталонные примеры."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.core import models
from contracts.ml_contracts import normalize_text

from .conftest import HEADER, ROWS, Stack, xlsx_bytes
from .openapi import assert_matches_schema

pytestmark = pytest.mark.db

FAIL_ROW = ["Позиция __fail__ для проверки ошибки", "", 1, "шт"]
# Коды каталога-фикстуры заглушки (contracts/fixtures/fake_catalog.jsonl).
CODES = [
    "99.99.01.000-00000001",
    "99.99.01.000-00000002",
    "99.99.02.000-00000001",
    "99.99.03.000-00000001",
    "99.99.03.000-00000002",
    "99.99.04.000-00000001",
    "99.99.05.000-00000001",
    "99.99.06.000-00000001",
]


def _items(stack: Stack, job_id: str, **params: Any) -> list[dict[str, Any]]:
    return stack.client.get(f"/api/v1/jobs/{job_id}/items", params={"page_size": 200, **params}).json()["items"]


def _item(stack: Stack, item_id: str) -> dict[str, Any]:
    return stack.client.get(f"/api/v1/items/{item_id}").json()


def _decisions(stack: Stack, item_id: str) -> list[tuple[Any, ...]]:
    with Session(stack.engine) as session:
        rows = session.execute(
            select(models.Decision.kind, models.Decision.via, models.Decision.ktru_code, models.Decision.old_code)
            .where(models.Decision.item_id == uuid.UUID(item_id))
            .order_by(models.Decision.created_at)
        ).all()
    return [tuple(r) for r in rows]


def _examples(stack: Stack) -> list[tuple[Any, ...]]:
    with Session(stack.engine) as session:
        rows = session.execute(
            select(
                models.ConfirmedExample.source_item_id,
                models.ConfirmedExample.ktru_code,
                models.ConfirmedExample.norm_text,
            )
        ).all()
    return [(str(item_id), code, norm) for item_id, code, norm in rows]


def _error(response: Any, status: int, code: str) -> dict[str, Any]:
    assert response.status_code == status, response.text
    body = response.json()
    assert_matches_schema("Error", body)
    assert body["error"]["code"] == code
    return body["error"]


def test_confirm_is_idempotent_and_becomes_an_example(stack: Stack) -> None:
    job = stack.finished_job(ROWS)
    item = _items(stack, job["job_id"])[0]
    top = item["predictions"][0]

    response = stack.client.post(f"/api/v1/items/{item['item_id']}/confirm")
    assert response.status_code == 200
    confirmed = response.json()
    assert_matches_schema("Item", confirmed)
    assert confirmed["status"] == "confirmed"
    final = {"ktru_code": top["ktru_code"], "name": top["name"], "source": "user", "confidence": None}
    assert confirmed["final"] == final
    assert confirmed["band"] == item["band"]  # решение человека цвет не меняет

    again = stack.client.post(f"/api/v1/items/{item['item_id']}/confirm")  # фронт прислал запрос дважды
    assert (again.status_code, again.json()) == (200, confirmed)
    assert _decisions(stack, item["item_id"]) == [("confirm", "manual", top["ktru_code"], top["ktru_code"])]
    assert _examples(stack) == [(item["item_id"], top["ktru_code"], normalize_text(item["raw_text"]))]
    assert stack.client.get(f"/api/v1/jobs/{job['job_id']}").json()["summary"]["confirmed"] == 1


def test_correct_then_change_mind(stack: Stack) -> None:
    job = stack.finished_job(ROWS)
    item = _items(stack, job["job_id"])[0]
    top = item["predictions"][0]["ktru_code"]
    other = next(code for code in CODES if code != top)
    url = f"/api/v1/items/{item['item_id']}/decision"

    corrected = stack.client.put(url, json={"ktru_code": f" {other} ", "reason": " так в ТЗ "}).json()
    assert_matches_schema("Item", corrected)
    assert corrected["status"] == "corrected"
    assert corrected["final"]["ktru_code"] == other
    assert (corrected["final"]["source"], corrected["final"]["confidence"]) == ("user", None)
    assert corrected["predictions"] == item["predictions"]  # предсказания остаются для карточки
    assert stack.client.put(url, json={"ktru_code": other, "reason": "так в ТЗ"}).status_code == 200  # повтор

    back = stack.client.put(url, json={"ktru_code": top}).json()  # код совпал с rank 1 — это подтверждение
    assert back["status"] == "confirmed"
    assert _decisions(stack, item["item_id"]) == [("correct", "manual", other, top), ("confirm", "manual", top, other)]
    assert _examples(stack) == [(item["item_id"], top, normalize_text(item["raw_text"]))]  # один пример на позицию
    with Session(stack.engine) as session:
        reasons = session.scalars(select(models.Decision.reason).order_by(models.Decision.created_at)).all()
    assert reasons == ["так в ТЗ", None]
    summary = stack.client.get(f"/api/v1/jobs/{job['job_id']}").json()["summary"]
    assert (summary["confirmed"], summary["corrected"]) == (1, 0)


def test_failed_item_gets_manual_code(stack: Stack) -> None:
    job = stack.finished_job([FAIL_ROW])
    failed = _items(stack, job["job_id"])[0]
    error = _error(stack.client.post(f"/api/v1/items/{failed['item_id']}/confirm"), 409, "invalid_state")
    assert error["details"] == {"status": "failed"}

    corrected = stack.client.put(f"/api/v1/items/{failed['item_id']}/decision", json={"ktru_code": CODES[5]}).json()
    assert (corrected["status"], corrected["predictions"], corrected["final"]["name"]) == (
        "corrected",
        [],
        "Бумага офисная",
    )
    assert _decisions(stack, failed["item_id"]) == [("correct", "manual", CODES[5], None)]
    error = _error(stack.client.post(f"/api/v1/items/{failed['item_id']}/confirm"), 409, "invalid_state")
    assert error["details"] == {"status": "corrected"}  # подтверждать по-прежнему нечего


def test_decisions_are_rejected_for_bad_input(stack: Stack) -> None:
    job = stack.create_job(stack.upload(xlsx_bytes([HEADER, *ROWS]))["file_id"])
    stack.parse_only(job["job_id"])  # позиции созданы, но ещё не обработаны
    pending = _items(stack, job["job_id"])[0]
    assert pending["status"] == "pending"
    for response in (
        stack.client.post(f"/api/v1/items/{pending['item_id']}/confirm"),
        stack.client.put(f"/api/v1/items/{pending['item_id']}/decision", json={"ktru_code": CODES[0]}),
    ):
        assert _error(response, 409, "invalid_state")["details"] == {"status": "pending"}

    done = _items(stack, stack.finished_job(ROWS)["job_id"])[0]
    url = f"/api/v1/items/{done['item_id']}/decision"
    error = _error(stack.client.put(url, json={"ktru_code": "00.00.00.000-00000000"}), 404, "not_found")
    assert error["details"] == {"object": "ktru_code"}
    _error(stack.client.put(url, json={"ktru_code": ""}), 400, "invalid_request")
    _error(stack.client.put(url, json={"ktru_code": CODES[0], "reason": "x" * 501}), 400, "invalid_request")
    _error(stack.client.post(f"/api/v1/items/{uuid.uuid4()}/confirm"), 404, "not_found")
    assert _decisions(stack, done["item_id"]) == []


def test_bulk_confirm(stack: Stack) -> None:
    rows = [[f"Товар номер {n}", "", 1, "шт"] for n in range(40)] + [FAIL_ROW]
    job_id = stack.finished_job(rows)["job_id"]
    items = _items(stack, job_id)
    flagged = [i for i in items if i["flags"]]
    eligible = [i for i in items if i["predictions"] and i["predictions"][0]["confidence"] >= 0.85 and not i["flags"]]
    # В наборе есть все случаи: с предупреждением, выше порога и ниже порога.
    assert flagged
    assert eligible
    assert len(eligible) < len(items) - 1

    manual = eligible[0]
    stack.client.post(f"/api/v1/items/{manual['item_id']}/confirm")
    url = f"/api/v1/jobs/{job_id}/confirm-bulk"
    response = stack.client.post(url, json={"min_confidence": 0.85})
    assert response.json() == {"confirmed_count": len(eligible) - 1}  # уже подтверждённая не считается
    after = {i["item_id"]: i for i in _items(stack, job_id)}
    assert {after[i["item_id"]]["status"] for i in eligible} == {"confirmed"}
    assert {after[i["item_id"]]["status"] for i in flagged} <= {"processed", "needs_review"}
    code = eligible[1]["predictions"][0]["ktru_code"]
    assert _decisions(stack, eligible[1]["item_id"]) == [("confirm", "bulk", code, code)]
    assert len(_examples(stack)) == 1  # массовое подтверждение не пишет эталонных примеров

    assert stack.client.post(url, json={"min_confidence": 0.85}).json() == {"confirmed_count": 0}
    rest = stack.client.post(url, json={"min_confidence": 0, "skip_flagged": False}).json()["confirmed_count"]
    assert rest == len(items) - 1 - len(eligible)  # всё, кроме упавшей позиции
    assert stack.client.get(f"/api/v1/jobs/{job_id}").json()["summary"]["confirmed"] == len(items) - 1

    # Ручное подтверждение после массового — уже проверка человеком: пишется решение manual и пример.
    stack.client.post(f"/api/v1/items/{eligible[1]['item_id']}/confirm")
    assert [d[1] for d in _decisions(stack, eligible[1]["item_id"])] == ["bulk", "manual"]
    assert len(_examples(stack)) == 2

    _error(stack.client.post(url, json={"min_confidence": 1.5}), 400, "invalid_request")
    _error(stack.client.post(url, json={}), 400, "invalid_request")
    unknown = stack.client.post(f"/api/v1/jobs/{uuid.uuid4()}/confirm-bulk", json={"min_confidence": 0.5})
    _error(unknown, 404, "not_found")


def test_bulk_confirm_during_processing(stack: Stack) -> None:
    rows = [[f"Товар номер {n}", "", 1, "шт"] for n in range(60)]
    job_id = stack.create_job(stack.upload(xlsx_bytes([HEADER, *rows]))["file_id"])["job_id"]
    first, second = stack.parse_only(job_id)
    stack.run_chunk(first)
    url = f"/api/v1/jobs/{job_id}/confirm-bulk"
    assert stack.client.post(url, json={"min_confidence": 0, "skip_flagged": False}).json() == {"confirmed_count": 50}
    stack.run_chunk(second)  # оставшиеся позиции воркер обрабатывает как обычно
    job = stack.client.get(f"/api/v1/jobs/{job_id}").json()
    assert (job["status"], job["processed_items"], job["summary"]["confirmed"]) == ("done", 60, 50)
