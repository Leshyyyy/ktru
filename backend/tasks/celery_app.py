"""Приложение Celery для worker-io и beat: `celery -A backend.tasks.celery_app worker -Q io`.

worker-ml запускается через backend.tasks.ml_worker — там же загрузка моделей, сигнал готовности и ml.process_chunk.
API ставит задачи через create_celery(settings) и backend.core.queue — модули задач ему импортировать не нужно.
"""

from __future__ import annotations

from typing import Any

from celery import Celery
from celery.signals import setup_logging as celery_setup_logging
from kombu import Queue

from backend.core.logs import setup_logging
from backend.core.queue import CLASSIFY_QUEUE
from backend.core.settings import Settings, get_settings


def create_celery(settings: Settings) -> Celery:
    celery = Celery("ktru", broker=settings.redis_url(), include=["backend.tasks.jobs", "backend.tasks.watchdog"])
    celery.conf.update(
        # Только JSON: pickle из брокера — это выполнение произвольного кода.
        task_serializer="json",
        result_serializer="json",
        accept_content=["json"],
        # Результаты задач пишутся в PostgreSQL. Бэкенд результатов (Redis) нужен только синхронному
        # POST /classify: его задача включает ignore_result=False, остальные результат не сохраняют.
        task_ignore_result=True,
        result_backend=settings.redis_url(),
        result_expires=600,
        # Пачка подтверждается после выполнения: упавший воркер не теряет её. При гибели процесса задача
        # не возвращается в очередь сама (иначе «ядовитая» пачка будет ронять воркер по кругу) — её подберёт
        # watchdog.
        task_acks_late=True,
        task_reject_on_worker_lost=False,
        worker_prefetch_multiplier=1,
        task_default_queue="io",
        task_queues=(Queue("io"), Queue("ml"), Queue(CLASSIFY_QUEUE)),
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
    return celery


settings = get_settings()
app = create_celery(settings)


@celery_setup_logging.connect
def _setup_logging(**_: Any) -> None:
    # Подписчик на сигнал отключает собственную настройку логов Celery: формат один на все сервисы.
    setup_logging(settings.log_level)
