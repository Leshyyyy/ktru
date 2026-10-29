"""Оценка качества (FR-9.1): размеченные наборы dev и test в eval_samples и прогоны в eval_runs.

Разметку загружает migrate из contracts/fixtures/eval/*.jsonl (одна строка — одна позиция, поля как у eval_samples).
Прогон считает worker-ml: ml.evaluate.evaluate() на том же конвейере, что обрабатывает задачи, с выключенными
подтверждёнными примерами (иначе метрики завышаются). Метрики — словарь evaluate() как есть (ml/CLAUDE.md).
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import timedelta
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ValidationError, field_validator
from sqlalchemy import Engine, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from contracts.ml_contracts import Pipeline

from .ml import evaluate_quality
from .models import EvalRun, EvalSample

log = logging.getLogger(__name__)

# Прогон без движения дольше этого — воркер упал посреди оценки (watchdog ставит failed).
STALE_RUN = timedelta(hours=1)
# Сколько ошибочных позиций сохранять в метриках: для разбора хватает, а ответ /quality/latest остаётся лёгким.
MAX_ERRORS = 100
RUN_FAILED = "Оценка не выполнена — подробности в логах worker-ml."
_DIFFICULTIES = ("easy", "medium", "hard")


class EvalSampleRow(BaseModel):
    """Строка файла разметки. Лишние поля (tier, заметки разметчика) пропускаются."""

    id: str
    split: Literal["dev", "test"]
    raw_text: str
    expected_ktru_code: str
    alt_valid_codes: list[str] | None = None
    expected_attrs: dict[str, Any] | None = None
    difficulty: str | None = None
    source: str | None = None

    @field_validator("difficulty")
    @classmethod
    def _known_difficulty(cls, value: str | None) -> str | None:
        return value if value in _DIFFICULTIES else None


def seed_eval_samples(engine: Engine, directory: Path) -> int:
    """Загружает разметку из *.jsonl каталога (upsert по id). Файл с ошибкой пропускается целиком: без разметки
    система работает, а /quality/run скажет, что набора нет. → сколько позиций загружено.
    """
    if not directory.is_dir():
        return 0
    loaded = 0
    for path in sorted(directory.glob("*.jsonl")):
        try:
            rows = _read_samples(path)
        except (OSError, ValueError) as e:
            log.error("Файл разметки пропущен: %s", e, extra={"file": path.name})
            continue
        if not rows:
            continue
        with engine.begin() as conn:
            stmt = insert(EvalSample).values([row.model_dump() for row in rows])
            stmt = stmt.on_conflict_do_update(
                index_elements=[EvalSample.id],
                set_={name: stmt.excluded[name] for name in EvalSampleRow.model_fields if name != "id"},
            )
            conn.execute(stmt)
        loaded += len(rows)
        log.info("Разметка загружена", extra={"file": path.name, "samples": len(rows)})
    return loaded


def _read_samples(path: Path) -> list[EvalSampleRow]:
    rows: dict[str, EvalSampleRow] = {}
    with path.open(encoding="utf-8") as f:
        for number, line in enumerate(f, start=1):
            if not line.strip():
                continue
            try:
                row = EvalSampleRow.model_validate(json.loads(line))
            except (json.JSONDecodeError, ValidationError) as e:
                raise ValueError(f"{path.name}, строка {number}: не соответствует формату eval_samples") from e
            rows[row.id] = row
    return list(rows.values())


def samples_count(session: Session, split: str) -> int:
    return session.scalar(select(func.count()).select_from(EvalSample).where(EvalSample.split == split)) or 0


def run_quality(engine: Engine, pipeline: Pipeline, run_id: uuid.UUID, mode: dict[str, Any]) -> None:
    """Задача ml.quality_run. Повторная доставка уже завершённого прогона ничего не делает."""
    with Session(engine) as session:
        run = session.get(EvalRun, run_id)
        if run is None or run.status != "running":
            return
        split, config = run.split, dict(run.config)
        samples = [
            {
                "id": s.id,
                "raw_text": s.raw_text,
                "expected_ktru_code": s.expected_ktru_code,
                "alt_valid_codes": s.alt_valid_codes or [],
                "expected_attrs": s.expected_attrs,
            }
            for s in session.scalars(select(EvalSample).where(EvalSample.split == split).order_by(EvalSample.id))
        ]
    config["mode"] = mode
    if not samples:
        config["error"] = f"В наборе {split} нет размеченных позиций."
        _finish(engine, run_id, "failed", config, None)
        return
    try:
        metrics = evaluate_quality(samples, pipeline, str(config.get("catalog_version_id") or ""))
    except Exception:
        log.exception("Оценка качества не выполнена", extra={"run_id": str(run_id), "split": split})
        config["error"] = RUN_FAILED
        _finish(engine, run_id, "failed", config, None)
        return
    metrics["errors"] = metrics.get("errors", [])[:MAX_ERRORS]
    _finish(engine, run_id, "done", config, metrics)
    log.info("Оценка качества завершена", extra={"run_id": str(run_id), "split": split, "samples": len(samples)})


def _finish(engine: Engine, run_id: uuid.UUID, status: str, config: dict[str, Any], metrics: Any) -> None:
    with engine.begin() as conn:
        conn.execute(
            update(EvalRun)
            .where(EvalRun.id == run_id, EvalRun.status == "running")
            .values(status=status, config=config, metrics=metrics)
        )


def fail_stale_runs(engine: Engine, stale_after: timedelta = STALE_RUN) -> int:
    """Прогоны в running дольше stale_after (worker-ml упал или перезапущен посреди оценки) → failed."""
    with engine.begin() as conn:
        failed = conn.execute(
            update(EvalRun)
            .where(EvalRun.status == "running", EvalRun.created_at < func.now() - stale_after)
            .values(
                status="failed",
                config=EvalRun.config.op("||")(
                    func.jsonb_build_object("error", "Оценка прервалась — запустите её заново.")
                ),
            )
            .returning(EvalRun.id)
        ).all()
    if failed:
        log.warning("Зависшие прогоны оценки помечены failed", extra={"count": len(failed)})
    return len(failed)
