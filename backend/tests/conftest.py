from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from sqlalchemy import make_url
from sqlalchemy.engine import URL

from backend.core.db import make_engine
from backend.core.settings import Settings


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Переменные окружения разработчика или CI не должны менять результат тестов."""
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)
    for name in ("EXTRACTOR", "RERANKER", "DEVICE", "LLM_API_KEY", "LLM_BASE_URL", "STUB_DEGRADED", "STUB_DELAY_MS"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("STUB_DELAY_MS", "0")


@pytest.fixture
def settings() -> Settings:
    return Settings(app_db_password="app-secret", redis_password="redis-secret")


class FakeRedis:
    """Минимальный Redis для тестов: get/set/delete/ping и отказ по флагу."""

    def __init__(self) -> None:
        self.data: dict[str, str] = {}
        self.ttl: dict[str, int | None] = {}
        self.broken = False

    def _check(self) -> None:
        if self.broken:
            from redis.exceptions import ConnectionError as RedisConnectionError

            raise RedisConnectionError("нет связи")

    def ping(self) -> bool:
        self._check()
        return True

    def set(self, name: str, value: str, ex: int | None = None) -> bool:
        self._check()
        self.data[name], self.ttl[name] = value, ex
        return True

    def get(self, name: str) -> Any:
        self._check()
        return self.data.get(name)

    def delete(self, *names: str) -> int:
        self._check()
        return sum(self.data.pop(n, None) is not None for n in names)

    def close(self) -> None:
        pass


@pytest.fixture
def fake_redis() -> FakeRedis:
    return FakeRedis()


# --- PostgreSQL для тестов с маркером db -------------------------------------------------------------------


@pytest.fixture(scope="session")
def admin_url() -> URL:
    raw = os.environ.get("TEST_DATABASE_URL")
    if not raw:
        pytest.skip("TEST_DATABASE_URL не задан: тесты с PostgreSQL пропущены")
    return make_url(raw)


def _admin_exec(admin_url: URL, statement: str) -> None:
    engine = make_engine(admin_url, application_name="ktru-tests", null_pool=True)
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        conn.exec_driver_sql(statement)
    engine.dispose()


@pytest.fixture
def db_settings(admin_url: URL) -> Iterator[Settings]:
    """Пустая база и своя роль приложения на каждый тест; после теста удаляются."""
    suffix = uuid.uuid4().hex[:10]
    database, role = f"ktru_test_{suffix}", f"ktru_app_{suffix}"
    _admin_exec(admin_url, f'CREATE DATABASE "{database}"')
    try:
        yield Settings(
            postgres_host=admin_url.host,
            postgres_port=admin_url.port or 5432,
            postgres_db=database,
            postgres_user=admin_url.username,
            postgres_password=admin_url.password,
            app_db_user=role,
            app_db_password="app-secret",
        )
    finally:
        _admin_exec(admin_url, f'DROP DATABASE IF EXISTS "{database}" WITH (FORCE)')
        _admin_exec(admin_url, f'DROP ROLE IF EXISTS "{role}"')
