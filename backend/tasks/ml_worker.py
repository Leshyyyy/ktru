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
import threading
import time
import uuid
from datetime import UTC, datetime
from typing import Any

from celery import Task
from celery.signals import worker_process_init, worker_process_shutdown
from redis import Redis

from backend.core import processing
from backend.core.catalog import CatalogSync
from backend.core.db import worker_engine
from backend.core.logs import job_id_var
from backend.core.ml import catalog_fallback, load_pipeline, pipeline_mode
from backend.core.quality import run_quality
from backend.core.queue import CLASSIFY, PROCESS_CHUNK, QUALITY_RUN
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


_stopping = threading.Event()


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


@app.task(name=CLASSIFY, ignore_result=False, soft_time_limit=60, time_limit=90)
def classify(texts: list[str], catalog_version_id: str) -> list[dict[str, Any]]:
    """POST /classify: ответ возвращается API через бэкенд результатов; ошибка — исключение у ожидающего API."""
    pipeline = get_pipeline()
    if pipeline is None:
        raise RuntimeError("Конвейер не загружен")
    return processing.classify_texts(worker_engine(settings), pipeline, texts, uuid.UUID(catalog_version_id))


@app.task(name=QUALITY_RUN, ignore_result=True, soft_time_limit=1800, time_limit=1900)
def quality_run(run_id: str) -> None:
    """POST /quality/run: оценка на размеченном наборе. Занимает очередь ml на время прогона (сотни позиций)."""
    pipeline = get_pipeline()
    if pipeline is None:
        raise RuntimeError("Конвейер не загружен")
    run_quality(worker_engine(settings), pipeline, uuid.UUID(run_id), pipeline_mode(pipeline, _effective_settings()))


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
    if _state["pipeline"] is not None:
        sync = CatalogSync(worker_engine(settings), _state["pipeline"])
        threading.Thread(target=_sync_catalog, args=(sync,), name="catalog-sync", daemon=True).start()


def _sync_catalog(sync: CatalogSync) -> None:
    """FR-8.1: раз в ML_HEARTBEAT_INTERVAL_S проверяет, нет ли новой версии каталога (сразу после старта — тоже)."""
    while True:
        try:
            sync.poll()
        except Exception:
            log.warning("Не удалось проверить версии каталога", exc_info=True)
        if _stopping.wait(settings.ml_heartbeat_interval_s):
            return


@worker_process_shutdown.connect
def _stop_heartbeat(**_: Any) -> None:
    _stopping.set()
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
        "catalog_versions": _loaded_versions(pipeline),
        "host": socket.gethostname(),
        "pid": os.getpid(),
        "at": datetime.now(UTC).isoformat(timespec="seconds"),
    }


def _loaded_versions(pipeline: Any) -> list[str] | None:
    """Версии каталога в памяти конвейера (для отладки FR-8.1); «» — каталог из файла KTRU_CATALOG_PATH."""
    loaded = getattr(pipeline, "loaded_versions", None)
    try:
        return list(loaded()) if loaded else None
    except Exception:
        return None
