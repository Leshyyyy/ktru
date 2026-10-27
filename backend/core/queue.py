"""Постановка задач в очереди Celery. API и воркеры зовут задачи по имени, не импортируя их модули:
так api не тянет за собой код воркеров, а worker-io — загрузку моделей worker-ml.
"""

from __future__ import annotations

from typing import Protocol

from celery import Celery

PARSE_FILE = "io.parse_file"
PROCESS_CHUNK = "ml.process_chunk"

# Брокер недоступен — не ждём дольше секунды: задача уже записана в базе, её подберёт watchdog.
_PUBLISH_RETRY = {"max_retries": 2, "interval_start": 0, "interval_step": 0.3, "interval_max": 0.5}


class TaskQueue(Protocol):
    def parse_file(self, job_id: str) -> None: ...

    def process_chunk(self, job_id: str, item_ids: list[str]) -> None: ...


class CeleryQueue:
    def __init__(self, app: Celery):
        self.app = app

    def parse_file(self, job_id: str) -> None:
        self.app.send_task(PARSE_FILE, args=[job_id], retry=True, retry_policy=_PUBLISH_RETRY)

    def process_chunk(self, job_id: str, item_ids: list[str]) -> None:
        self.app.send_task(PROCESS_CHUNK, args=[job_id, item_ids], retry=True, retry_policy=_PUBLISH_RETRY)
