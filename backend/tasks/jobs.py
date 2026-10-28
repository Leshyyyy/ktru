"""Задачи очереди io для обработки спецификаций. Логика — в backend/core/processing.py, здесь только вход Celery."""

from __future__ import annotations

import logging
import uuid

from backend.core.db import worker_engine
from backend.core.logs import job_id_var
from backend.core.processing import parse_job
from backend.core.queue import PARSE_FILE, CeleryQueue

from .celery_app import app, settings

log = logging.getLogger(__name__)


# Меньше 5 минут, после которых watchdog считает разбор зависшим и запускает его заново.
@app.task(name=PARSE_FILE, ignore_result=True, soft_time_limit=240, time_limit=270)
def parse_file(job_id: str) -> None:
    try:
        job_uuid = uuid.UUID(job_id)
    except ValueError:
        log.error("parse_file: некорректный job_id")
        return
    token = job_id_var.set(job_id)
    try:
        parse_job(worker_engine(settings), settings, CeleryQueue(app), job_uuid)
    finally:
        job_id_var.reset(token)
