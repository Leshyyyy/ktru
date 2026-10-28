"""Постановка задач в очереди Celery. API и воркеры зовут задачи по имени, не импортируя их модули:
так api не тянет за собой код воркеров, а worker-io — загрузку моделей worker-ml.
"""

from __future__ import annotations

from typing import Any, Protocol

from celery import Celery
from celery.exceptions import TimeoutError as CeleryTimeoutError

PARSE_FILE = "io.parse_file"
PROCESS_CHUNK = "ml.process_chunk"
CLASSIFY = "ml.classify"
# Отдельная очередь для POST /classify: worker-ml слушает её вместе с ml, и запрос ждёт не больше одной пачки,
# а не все пачки большой задачи.
CLASSIFY_QUEUE = "classify"

# Брокер недоступен — не ждём дольше секунды: задача уже записана в базе, её подберёт watchdog.
_PUBLISH_RETRY = {"max_retries": 2, "interval_start": 0, "interval_step": 0.3, "interval_max": 0.5}


class ClassifyTimeout(Exception):
    """worker-ml не ответил на POST /classify за отведённое время (занят или не запущен)."""


class TaskQueue(Protocol):
    def parse_file(self, job_id: str) -> None: ...

    def process_chunk(self, job_id: str, item_ids: list[str]) -> None: ...

    def classify(self, texts: list[str], catalog_version_id: str, timeout_s: float) -> list[dict[str, Any]]: ...


class CeleryQueue:
    def __init__(self, app: Celery):
        self.app = app

    def parse_file(self, job_id: str) -> None:
        self.app.send_task(PARSE_FILE, args=[job_id], retry=True, retry_policy=_PUBLISH_RETRY)

    def process_chunk(self, job_id: str, item_ids: list[str]) -> None:
        self.app.send_task(PROCESS_CHUNK, args=[job_id, item_ids], retry=True, retry_policy=_PUBLISH_RETRY)

    def classify(self, texts: list[str], catalog_version_id: str, timeout_s: float) -> list[dict[str, Any]]:
        """Синхронный вызов worker-ml: ответ приходит через бэкенд результатов (Redis). Запрос, который
        не успели взять за timeout_s, воркер выбрасывает сам (expires): клиент его уже не ждёт.
        """
        result = self.app.send_task(
            CLASSIFY,
            args=[texts, catalog_version_id],
            queue=CLASSIFY_QUEUE,
            expires=timeout_s,
            retry=True,
            retry_policy=_PUBLISH_RETRY,
        )
        try:
            return result.get(timeout=timeout_s)
        except CeleryTimeoutError as e:
            raise ClassifyTimeout from e
        finally:
            result.forget()
