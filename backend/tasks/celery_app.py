"""Приложение Celery для worker-io и beat: `celery -A backend.tasks.celery_app worker -Q io`.

worker-ml запускается через backend.tasks.ml_worker — там же загрузка моделей и сигнал готовности.
"""

from __future__ import annotations

from typing import Any

from celery import Celery
from celery.signals import setup_logging as celery_setup_logging
from kombu import Queue

from backend.core.logs import setup_logging
from backend.core.settings import get_settings

settings = get_settings()

app = Celery("ktru", broker=settings.redis_url(), include=["backend.tasks.watchdog"])
app.conf.update(
    # Только JSON: pickle из брокера — это выполнение произвольного кода.
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    # Результаты задач пишутся в PostgreSQL, бэкенд результатов Celery не нужен.
    task_ignore_result=True,
    # Пачка подтверждается после выполнения: упавший воркер не теряет её. При гибели процесса задача
    # не возвращается в очередь сама (иначе «ядовитая» пачка будет ронять воркер по кругу) — её подберёт watchdog.
    task_acks_late=True,
    task_reject_on_worker_lost=False,
    worker_prefetch_multiplier=1,
    task_default_queue="io",
    task_queues=(Queue("io"), Queue("ml")),
    task_routes={"io.*": {"queue": "io"}, "ml.*": {"queue": "ml"}},
    # Лимиты пачки (раздел 6 аналитики); задачам с другим временем работы лимит задаётся в @app.task.
    task_soft_time_limit=120,
    task_time_limit=180,
    broker_connection_retry_on_startup=True,
    # Неподтверждённая задача вернётся в очередь через час — это больше любого лимита задачи.
    broker_transport_options={"visibility_timeout": 3600},
    worker_hijack_root_logger=False,
    worker_send_task_events=False,
    timezone="UTC",
    enable_utc=True,
    beat_schedule={"watchdog": {"task": "io.watchdog", "schedule": 60.0}},
)


@celery_setup_logging.connect
def _setup_logging(**_: Any) -> None:
    # Подписчик на сигнал отключает собственную настройку логов Celery: формат один на все сервисы.
    setup_logging(settings.log_level)
