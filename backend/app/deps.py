"""Зависимости FastAPI. Состояние приложения (настройки, движок, Redis) создаётся в lifespan и лежит в app.state."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import Request
from redis.exceptions import RedisError

from backend.core.readiness import read_status
from backend.core.settings import Settings

from .health import HealthChecker

log = logging.getLogger(__name__)


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_health_checker(request: Request) -> HealthChecker:
    return HealthChecker(request.app.state.engine, request.app.state.redis)


def get_ml_status(request: Request) -> dict[str, Any] | None:
    """Состояние, которое пишет worker-ml; None — воркера нет или Redis недоступен."""
    try:
        return read_status(request.app.state.redis)
    except RedisError:
        log.warning("Не удалось прочитать состояние ML-воркера", exc_info=True)
        return None
