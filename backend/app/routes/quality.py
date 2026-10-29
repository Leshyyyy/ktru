"""Тег quality: POST /quality/run и GET /quality/latest (FR-9.1). Оценку считает worker-ml (core/quality.py)."""

from __future__ import annotations

import logging
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.core import models
from backend.core.catalog import latest_ready_version
from backend.core.quality import samples_count
from backend.core.queue import TaskQueue

from .. import schemas
from ..deps import get_db, get_ml_status, get_queue
from ..errors import ApiError, not_ready

log = logging.getLogger(__name__)
router = APIRouter(tags=["quality"])

SessionDep = Annotated[Session, Depends(get_db)]
# Список ошибочных позиций остаётся в eval_runs.metrics для разбора, но в ответ не идёт: он большой.
_HIDDEN_METRICS = ("errors",)


@router.post("/quality/run", status_code=202, response_model=schemas.QualityRun)
def run_quality(
    body: schemas.QualityRunCreate,
    session: SessionDep,
    queue: Annotated[TaskQueue, Depends(get_queue)],
    ml_status: Annotated[dict[str, Any] | None, Depends(get_ml_status)],
) -> Any:
    """Прогон этого набора уже идёт — возвращается он, второй не запускается."""
    running = session.scalar(
        select(models.EvalRun)
        .where(models.EvalRun.split == body.split, models.EvalRun.status == "running")
        .order_by(models.EvalRun.created_at.desc())
        .limit(1)
    )
    if running is not None:
        return JSONResponse(status_code=202, content=quality_run(running).model_dump(mode="json"))
    if not samples_count(session, body.split):
        message = f"В наборе {body.split} нет размеченных позиций: добавьте разметку в contracts/fixtures/eval/."
        raise ApiError(400, "invalid_request", message, {"field": "split"})
    version_id = latest_ready_version(session)
    if version_id is None:
        raise not_ready()
    status = ml_status or {}
    run = models.EvalRun(
        id=uuid.uuid4(),
        split=body.split,
        status="running",
        # use_feedback всегда false: подтверждённые примеры завысили бы метрики (schema.sql, eval_runs.config).
        config={
            "catalog_version_id": str(version_id),
            "use_feedback": False,
            "pipeline": status.get("pipeline"),
            "mode": status.get("mode"),
        },
    )
    session.add(run)
    session.commit()
    try:
        queue.quality_run(str(run.id))
    except Exception:
        # Прогон уже в базе в статусе running: через час watchdog пометит его failed, и его можно запустить снова.
        log.exception("Не удалось поставить оценку в очередь", extra={"run_id": str(run.id)})
    session.refresh(run)
    return quality_run(run)


# Набор без прогонов в ответе отсутствует (в контракте dev и test — QualityRun, не null).
@router.get("/quality/latest", response_model=schemas.QualityLatest, response_model_exclude_unset=True)
def latest_quality(session: SessionDep) -> schemas.QualityLatest:
    runs = {}
    for split in ("dev", "test"):
        run = session.scalar(
            select(models.EvalRun)
            .where(models.EvalRun.split == split)
            .order_by(models.EvalRun.created_at.desc())
            .limit(1)
        )
        if run is not None:
            runs[split] = quality_run(run)
    return schemas.QualityLatest(**runs)


def quality_run(run: models.EvalRun) -> schemas.QualityRun:
    metrics = None
    if run.metrics is not None:
        metrics = {k: v for k, v in run.metrics.items() if v is not None and k not in _HIDDEN_METRICS}
    return schemas.QualityRun(
        run_id=run.id,
        split=run.split,
        status=run.status,
        config=run.config,
        metrics=metrics,
        created_at=run.created_at,
    )
