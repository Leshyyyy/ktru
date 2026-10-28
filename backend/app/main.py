"""Приложение FastAPI. Запуск: uvicorn --factory backend.app.main:create_app (см. docker-compose.yml)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from redis import Redis
from starlette.middleware.cors import CORSMiddleware

from backend.core.db import api_engine
from backend.core.logs import setup_logging
from backend.core.queue import CeleryQueue
from backend.core.settings import Settings, get_settings
from backend.tasks.celery_app import create_celery

from .errors import install_error_handlers
from .middleware import REQUEST_ID_HEADER, BodySizeLimitMiddleware, RequestContextMiddleware
from .routes import catalog, classify, export, files, items, jobs, system

API_PREFIX = "/api/v1"
# Запас сверх лимита файла на служебные части multipart. Точный размер файла проверяет POST /files.
_MULTIPART_OVERHEAD = 1024 * 1024


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    # Ни движок, ни клиент Redis не подключаются при создании: API стартует, даже если база ещё не готова.
    app.state.engine = api_engine(settings)
    app.state.redis = Redis.from_url(
        settings.redis_url(), socket_timeout=1.5, socket_connect_timeout=1.5, health_check_interval=30
    )
    # Только постановка задач: API задачи не выполняет, к брокеру подключается при первой отправке.
    celery = create_celery(settings)
    app.state.queue = CeleryQueue(celery)
    try:
        yield
    finally:
        celery.close()
        app.state.redis.close()
        app.state.engine.dispose()


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    setup_logging(settings.log_level)

    app = FastAPI(
        title="AI-классификатор КТРУ",
        version="1.0.0",
        lifespan=_lifespan,
        docs_url=f"{API_PREFIX}/docs" if settings.api_docs else None,
        redoc_url=None,
        openapi_url=f"{API_PREFIX}/openapi.json" if settings.api_docs else None,
    )
    app.state.settings = settings
    install_error_handlers(app, max_file_mb=settings.max_file_mb)
    for module in (files, jobs, export, items, classify, catalog, system):
        app.include_router(module.router, prefix=API_PREFIX)

    # add_middleware ставит новый слой снаружи: порядок от внешнего — CORS, контекст запроса, лимит тела.
    app.add_middleware(
        BodySizeLimitMiddleware,
        max_bytes=settings.max_file_mb * 1024 * 1024 + _MULTIPART_OVERHEAD,
        max_file_mb=settings.max_file_mb,
    )
    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["Content-Type", REQUEST_ID_HEADER],
        expose_headers=["X-Unreviewed-Count", "Content-Disposition", REQUEST_ID_HEADER],
        allow_credentials=False,
        max_age=600,
    )
    return app
