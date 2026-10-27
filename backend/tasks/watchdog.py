"""watchdog (FR-2.5): beat запускает его раз в минуту в очереди io."""

from __future__ import annotations

from backend.core.db import worker_engine
from backend.core.processing import requeue_stale_jobs
from backend.core.queue import CeleryQueue

from .celery_app import app, settings


@app.task(name="io.watchdog", ignore_result=True)
def watchdog() -> None:
    """Задачи в queued, parsing или processing без обновлений дольше 5 минут → заново в очередь их разбор
    или pending-позиции (backend/core/processing.py: requeue_stale_jobs).
    """
    requeue_stale_jobs(worker_engine(settings), CeleryQueue(app))
