"""Оценка качества (FR-9.1): разметка в eval_samples, POST /quality/run, задача worker-ml, GET /quality/latest."""

from __future__ import annotations

import json
import uuid
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import func, update
from sqlalchemy.orm import Session

from backend.core import models, quality
from contracts.stub_pipeline import StubPipeline

from .conftest import Stack
from .openapi import assert_matches_schema

pytestmark = pytest.mark.db

SAMPLES = [
    {
        "id": "s-0001",
        "split": "dev",
        "raw_text": "Ноутбук Lenovo IdeaPad 3, 16 ГБ",
        "expected_ktru_code": "99.99.01.000-00000001",
        "alt_valid_codes": ["99.99.01.000-00000002"],
        "expected_attrs": {"name": "Ноутбук", "brand": "Lenovo", "model": "IdeaPad 3"},
        "tier": "core",  # лишнее поле разметчика — пропускается
    },
    {"id": "s-0002", "split": "dev", "raw_text": "Бумага офисная А4", "expected_ktru_code": "99.99.04.000-00000001"},
    {
        "id": "s-0003",
        "split": "dev",
        "raw_text": "Шприц одноразовый 5 мл",
        "expected_ktru_code": "99.99.06.000-00000001",
        "difficulty": "очень сложно",  # не из списка схемы — пишется NULL
    },
]
MODE = {"extractor": "rules", "reranker": "none", "device": "cpu", "llm_enabled": False}


def _write(directory: Path, name: str, rows: list[dict[str, Any]] | str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    text = rows if isinstance(rows, str) else "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n"
    (directory / name).write_text(text, encoding="utf-8")


def _run(stack: Stack, split: str = "dev") -> Any:
    return stack.client.post("/api/v1/quality/run", json={"split": split})


def test_seed_eval_samples(stack: Stack, tmp_path: Path) -> None:
    directory = tmp_path / "eval"
    _write(directory, "dev.jsonl", SAMPLES)
    _write(directory, "broken.jsonl", '{"id": "s-9", "split": "dev"}\n')  # без текста — файл пропускается
    assert quality.seed_eval_samples(stack.engine, directory) == 3
    assert quality.seed_eval_samples(stack.engine, directory) == 3  # повтор обновляет, а не дублирует
    assert quality.seed_eval_samples(stack.engine, tmp_path / "missing") == 0
    with Session(stack.engine) as session:
        first = session.get(models.EvalSample, "s-0001")
        third = session.get(models.EvalSample, "s-0003")
        assert quality.samples_count(session, "dev") == 3
    assert first.alt_valid_codes == ["99.99.01.000-00000002"]
    assert third.difficulty is None


def test_quality_run_and_latest(stack: Stack, tmp_path: Path) -> None:
    response = _run(stack)
    assert response.status_code == 400  # разметки нет — понятная ошибка, а не пустой прогон
    assert "нет размеченных позиций" in response.json()["error"]["message"]

    _write(tmp_path / "eval", "dev.jsonl", SAMPLES)
    quality.seed_eval_samples(stack.engine, tmp_path / "eval")
    response = _run(stack)
    assert response.status_code == 202, response.text
    run = response.json()
    assert_matches_schema("QualityRun", run)
    assert (run["status"], run["config"]["use_feedback"], run["metrics"]) == ("running", False, None)
    assert _run(stack).json()["run_id"] == run["run_id"]  # второй прогон того же набора не запускается
    assert stack.queue.quality == [run["run_id"]]

    quality.run_quality(stack.engine, StubPipeline(), uuid.UUID(run["run_id"]), MODE)
    quality.run_quality(stack.engine, StubPipeline(), uuid.UUID(run["run_id"]), MODE)  # повтор ничего не меняет
    latest = stack.client.get("/api/v1/quality/latest").json()
    assert set(latest) == {"dev"}
    assert_matches_schema("QualityRun", latest["dev"])
    dev = latest["dev"]
    assert (dev["status"], dev["config"]["mode"]) == ("done", MODE)
    assert dev["metrics"]["samples"] == 3
    assert {"top1_full_code", "top1_okpd2", "top3", "ece", "fast_path_share", "seconds_per_1000"} <= set(dev["metrics"])
    assert "errors" not in dev["metrics"]  # список ошибок — только в базе
    assert _run(stack).json()["run_id"] != run["run_id"]  # прошлый завершён — новый запускается


def test_quality_run_failures(stack: Stack) -> None:
    empty, stale = uuid.uuid4(), uuid.uuid4()
    with Session(stack.engine) as session, session.begin():
        session.add_all(
            [
                models.EvalRun(id=empty, split="test", status="running", config={}),
                models.EvalRun(id=stale, split="dev", status="running", config={}),
            ]
        )
    quality.run_quality(stack.engine, StubPipeline(), empty, MODE)
    with Session(stack.engine) as session, session.begin():
        assert session.get(models.EvalRun, empty).config["error"] == "В наборе test нет размеченных позиций."
        old = func.now() - timedelta(hours=2)
        session.execute(update(models.EvalRun).where(models.EvalRun.id == stale).values(created_at=old))
    assert quality.fail_stale_runs(stack.engine) == 1
    latest = stack.client.get("/api/v1/quality/latest").json()
    assert (latest["test"]["status"], latest["dev"]["status"]) == ("failed", "failed")
    assert latest["dev"]["config"]["error"] == "Оценка прервалась — запустите её заново."
