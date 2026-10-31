"""Зависимости FastAPI. Состояние приложения (настройки, движок, Redis, очередь) создаётся в lifespan
и лежит в app.state.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from typing import Any

from fastapi import Request
from redis.exceptions import RedisError
from sqlalchemy.orm import Session

from backend.core.queue import TaskQueue
from backend.core.readiness import read_status
from backend.core.settings import Settings

from .health import HealthChecker

log = logging.getLogger(__name__)


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_db(request: Request) -> Iterator[Session]:
    """Сессия на запрос. Пишущие маршруты сами вызывают commit; без него всё откатывается при закрытии."""
    with Session(request.app.state.engine) as session:
        yield session


def get_queue(request: Request) -> TaskQueue:
    return request.app.state.queue


def get_health_checker(request: Request) -> HealthChecker:
    return HealthChecker(request.app.state.engine, request.app.state.redis)


def get_ml_status(request: Request) -> dict[str, Any] | None:
    """Состояние, которое пишет worker-ml; None — воркера нет или Redis недоступен."""
    try:
        return read_status(request.app.state.redis)
    except RedisError:
        log.warning("Не удалось прочитать состояние ML-воркера", exc_info=True)
        return None
