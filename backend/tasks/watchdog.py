"""Периодические задачи io: watchdog (FR-2.5) раз в минуту и уборка загруженных файлов раз в час (beat)."""

from __future__ import annotations

from datetime import timedelta

from backend.core.catalog import fail_stale_imports
from backend.core.db import worker_engine
from backend.core.processing import requeue_stale_jobs
from backend.core.quality import fail_stale_runs
from backend.core.queue import CeleryQueue
from backend.core.uploads import cleanup_uploads

from .celery_app import app, settings


@app.task(name="io.watchdog", ignore_result=True)
def watchdog() -> None:
    """Задачи в queued, parsing или processing без обновлений дольше 5 минут → заново в очередь их разбор
    или pending-позиции (backend/core/processing.py: requeue_stale_jobs).
    """
    engine = worker_engine(settings)
    requeue_stale_jobs(engine, CeleryQueue(app))
    fail_stale_runs(engine)
    fail_stale_imports(engine)


@app.task(name="io.cleanup_uploads", ignore_result=True)
def cleanup() -> None:
    """Файлы, по которым за UPLOAD_TTL_HOURS не создали задачу, и обрывки загрузок (core/uploads.py)."""
    cleanup_uploads(worker_engine(settings), settings.upload_dir, timedelta(hours=settings.upload_ttl_hours))
