"""Точка входа worker-ml: `celery -A backend.tasks.ml_worker worker -Q ml`.

Конвейер загружается один раз в каждом дочернем процессе — после fork, иначе соединения и потоки библиотек
делились бы между процессами. Затем процесс раз в ML_HEARTBEAT_INTERVAL_S пишет в Redis свою готовность
(её читает GET /health) и режим работы (его отдаёт GET /config). Перед каждой пачкой процесс досылает
в конвейер новые подтверждённые примеры (processing.ExampleFeed, FR-4.7).
"""

from __future__ import annotations

import logging
import os
import socket
import time
import uuid
from datetime import UTC, datetime
from typing import Any

from celery import Task
from celery.signals import worker_process_init, worker_process_shutdown
from redis import Redis

from backend.core import processing
from backend.core.db import worker_engine
from backend.core.logs import job_id_var
from backend.core.ml import catalog_fallback, load_pipeline, pipeline_mode
from backend.core.queue import PROCESS_CHUNK
from backend.core.readiness import Heartbeat
from backend.core.settings import Settings

from .celery_app import app, settings

log = logging.getLogger(__name__)

# Паузы между попытками пачки (раздел 6 аналитики); после последней позиции пачки получают статус failed.
RETRY_DELAYS_S = (5, 20, 60)
CHUNK_FAILED = "Не удалось обработать позицию за несколько попыток. Код можно указать вручную."

# Загрузка моделей идёт в worker_process_init, а Celery по умолчанию ждёт старта дочернего процесса 4 с.
app.conf.worker_proc_alive_timeout = settings.ml_worker_init_timeout_s

_state: dict[str, Any] = {
    "pipeline": None,
    "error": None,
    "fallback": None,  # PIPELINE=real, но каталога нет — работает заглушка (core/ml.catalog_fallback)
    "heartbeat": None,
    "examples": processing.ExampleFeed(),
}


def _effective_settings() -> Settings:
    return settings.model_copy(update={"pipeline": "stub"}) if _state["fallback"] else settings


def get_pipeline() -> Any:
    """Конвейер этого процесса для задач очереди ml. None — модели не загрузились (см. /health)."""
    return _state["pipeline"]


@app.task(name=PROCESS_CHUNK, bind=True, ignore_result=True, max_retries=len(RETRY_DELAYS_S))
def process_chunk(self: Task, job_id: str, item_ids: list[str]) -> None:
    try:
        job_uuid, item_uuids = uuid.UUID(job_id), [uuid.UUID(i) for i in item_ids]
    except (TypeError, ValueError):
        log.error("process_chunk: некорректные идентификаторы")
        return
    token = job_id_var.set(job_id)
    try:
        pipeline = get_pipeline()
        if pipeline is None:
            raise RuntimeError("Конвейер не загружен")
        processing.process_chunk(
            worker_engine(settings), settings, pipeline, job_uuid, item_uuids, examples=_state["examples"]
        )
    except Exception as exc:
        attempt = self.request.retries
        if attempt < len(RETRY_DELAYS_S):
            log.warning("Пачка не обработана, повторю", extra={"attempt": attempt + 1}, exc_info=True)
            raise self.retry(exc=exc, countdown=RETRY_DELAYS_S[attempt]) from exc
        log.exception("Пачка не обработана после всех попыток")
        processing.fail_chunk(worker_engine(settings), job_uuid, item_uuids, CHUNK_FAILED)
    finally:
        job_id_var.reset(token)


@worker_process_init.connect
def _load_pipeline(**_: Any) -> None:
    started_at = time.perf_counter()
    if fallback := catalog_fallback(settings):
        # Тот же признак, что у migrate: он в этом случае загрузил каталог-фикстуру.
        log.error(fallback)
        _state["fallback"] = fallback
    effective = _effective_settings()
    try:
        _state["pipeline"] = load_pipeline(effective)
        log.info(
            "Конвейер загружен",
            extra={"pipeline": effective.pipeline, "load_ms": round((time.perf_counter() - started_at) * 1000)},
        )
    except Exception:
        log.exception("Не удалось загрузить конвейер")
        _state["error"] = "Не удалось загрузить модели — подробности в логах worker-ml."

    redis = Redis.from_url(settings.redis_url(), socket_timeout=2, socket_connect_timeout=2)
    heartbeat = Heartbeat(redis, _payload, settings.ml_heartbeat_interval_s, settings.ml_ready_ttl_s)
    heartbeat.start()
    _state["heartbeat"] = heartbeat


@worker_process_shutdown.connect
def _stop_heartbeat(**_: Any) -> None:
    if heartbeat := _state["heartbeat"]:
        heartbeat.stop()


def _payload() -> dict[str, Any]:
    pipeline = _state["pipeline"]
    ready = {"models": False, "catalog": False}
    if pipeline is not None:
        try:
            ready = pipeline.is_ready()
        except Exception:
            log.exception("Pipeline.is_ready() упал")
    return {
        "models": bool(ready.get("models")),
        "catalog": bool(ready.get("catalog")),
        "pipeline": _effective_settings().pipeline,
        "mode": pipeline_mode(pipeline, _effective_settings()),
        "error": _state["error"],
        "fallback": _state["fallback"],
        "host": socket.gethostname(),
        "pid": os.getpid(),
        "at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
