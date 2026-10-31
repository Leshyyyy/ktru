"""Задачи очереди io: разбор спецификаций и импорт каталога. Логика — в backend/core/, здесь только вход Celery."""

from __future__ import annotations

import logging
import uuid
from pathlib import Path

from backend.core.catalog import run_catalog_import
from backend.core.db import worker_engine
from backend.core.logs import job_id_var
from backend.core.processing import parse_job
from backend.core.queue import IMPORT_CATALOG, PARSE_FILE, CeleryQueue

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


# Полный КТРУ — десятки тысяч строк: дольше лимита пачки, но меньше STALE_IMPORT в core/catalog.py.
@app.task(name=IMPORT_CATALOG, ignore_result=True, soft_time_limit=1800, time_limit=1900)
def import_catalog(version_id: str, storage_path: str) -> None:
    try:
        version_uuid = uuid.UUID(version_id)
    except ValueError:
        log.error("import_catalog: некорректный version_id")
        return
    run_catalog_import(worker_engine(settings), version_uuid, settings.upload_dir / Path(storage_path).name)
