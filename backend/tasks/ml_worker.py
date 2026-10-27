"""Точка входа worker-ml: `celery -A backend.tasks.ml_worker worker -Q ml`.

Конвейер загружается один раз в каждом дочернем процессе — после fork, иначе соединения и потоки библиотек
делились бы между процессами. Затем процесс раз в ML_HEARTBEAT_INTERVAL_S пишет в Redis свою готовность
(её читает GET /health) и режим работы (его отдаёт GET /config).
"""

from __future__ import annotations

import logging
import os
import socket
import time
from datetime import UTC, datetime
from typing import Any

from celery.signals import worker_process_init, worker_process_shutdown
from redis import Redis

from backend.core.ml import describe_mode, load_pipeline
from backend.core.readiness import Heartbeat

from .celery_app import app, settings

log = logging.getLogger(__name__)

# Загрузка моделей идёт в worker_process_init, а Celery по умолчанию ждёт старта дочернего процесса 4 с.
app.conf.worker_proc_alive_timeout = settings.ml_worker_init_timeout_s

_state: dict[str, Any] = {"pipeline": None, "error": None, "heartbeat": None}


def get_pipeline() -> Any:
    """Конвейер этого процесса для задач очереди ml. None — модели не загрузились (см. /health)."""
    return _state["pipeline"]


@worker_process_init.connect
def _load_pipeline(**_: Any) -> None:
    started_at = time.perf_counter()
    try:
        _state["pipeline"] = load_pipeline(settings)
        log.info(
            "Конвейер загружен",
            extra={"pipeline": settings.pipeline, "load_ms": round((time.perf_counter() - started_at) * 1000)},
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
        "pipeline": settings.pipeline,
        "mode": describe_mode(settings),
        "error": _state["error"],
        "host": socket.gethostname(),
        "pid": os.getpid(),
        "at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
