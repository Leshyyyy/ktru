"""Подключение к PostgreSQL. Драйвер один — psycopg 3, синхронно и в API, и в воркерах."""

from __future__ import annotations

import os
from typing import Any

from sqlalchemy import Engine, create_engine
from sqlalchemy.engine import URL
from sqlalchemy.pool import NullPool

from .settings import Settings


def make_engine(
    url: URL,
    *,
    application_name: str,
    connect_timeout_s: int = 3,
    statement_timeout_ms: int | None = None,
    pool_size: int = 5,
    max_overflow: int = 5,
    null_pool: bool = False,
) -> Engine:
    connect_args: dict[str, Any] = {"connect_timeout": connect_timeout_s, "application_name": application_name}
    if statement_timeout_ms:
        connect_args["options"] = f"-c statement_timeout={int(statement_timeout_ms)}"
    if null_pool:
        return create_engine(url, poolclass=NullPool, connect_args=connect_args)
    return create_engine(
        url,
        pool_pre_ping=True,
        pool_size=pool_size,
        max_overflow=max_overflow,
        pool_recycle=1800,
        connect_args=connect_args,
    )


def api_engine(settings: Settings) -> Engine:
    return make_engine(
        settings.app_database_url(),
        application_name="ktru-api",
        connect_timeout_s=settings.db_connect_timeout_s,
        statement_timeout_ms=settings.api_statement_timeout_ms,
    )


_worker_engine: tuple[int, Engine] | None = None


def worker_engine(settings: Settings) -> Engine:
    """Движок воркера Celery. Создаётся в каждом процессе заново: пул соединений нельзя делить через fork."""
    global _worker_engine
    pid = os.getpid()
    if _worker_engine is None or _worker_engine[0] != pid:
        engine = make_engine(
            settings.app_database_url(),
            application_name="ktru-worker",
            connect_timeout_s=settings.db_connect_timeout_s,
            pool_size=2,
            max_overflow=2,
        )
        _worker_engine = (pid, engine)
    return _worker_engine[1]
