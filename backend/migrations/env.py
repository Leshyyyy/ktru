"""Окружение Alembic. Схема задаётся SQL-ревизиями по contracts/schema.sql, автогенерации по моделям нет."""

from __future__ import annotations

from alembic import context

from backend.core.db import make_engine
from backend.core.settings import get_settings

config = context.config


def _run(connection) -> None:
    context.configure(connection=connection, target_metadata=None, transaction_per_migration=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    # migrate и тесты передают готовое соединение; иначе — подключение владельцем схемы из POSTGRES_*.
    connection = config.attributes.get("connection")
    if connection is not None:
        _run(connection)
        return
    engine = make_engine(get_settings().owner_database_url(), application_name="ktru-alembic", null_pool=True)
    with engine.connect() as conn:
        _run(conn)


if context.is_offline_mode():
    raise SystemExit("Offline-режим не поддерживается: миграции применяются к живой базе.")
run_migrations_online()
