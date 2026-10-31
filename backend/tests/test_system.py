"""GET /health и GET /config: значения и соответствие схемам Health и PublicConfig из openapi.yaml."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from backend.app import deps
from backend.app.health import HealthChecker
from backend.app.main import create_app
from backend.core.ml import CATALOG_FALLBACK
from backend.core.readiness import write_status
from backend.core.settings import Settings

from .conftest import FakeRedis
from .openapi import assert_matches_schema

STUB_STATUS = {
    "models": True,
    "catalog": True,
    "pipeline": "stub",
    "mode": {"extractor": "rules", "reranker": "none", "device": "cpu", "llm_enabled": False},
    "error": None,
}


class FakeResult:
    def __init__(self, row: Any) -> None:
        self.row = row

    def first(self) -> Any:
        return self.row


class FakeConnection:
    def __init__(self, engine: FakeEngine) -> None:
        self.engine = engine

    def __enter__(self) -> FakeConnection:
        if self.engine.broken:
            raise OperationalError("SELECT 1", {}, Exception("нет связи"))
        return self

    def __exit__(self, *_: Any) -> None:
        pass

    def exec_driver_sql(self, _: str) -> None:
        pass

    def execute(self, _: Any) -> FakeResult:
        return FakeResult(self.engine.catalog_row)


class FakeEngine:
    def __init__(self, catalog_row: Any = None, broken: bool = False) -> None:
        self.catalog_row = catalog_row
        self.broken = broken

    def connect(self) -> FakeConnection:
        return FakeConnection(self)


def _catalog(items: int = 8) -> SimpleNamespace:
    return SimpleNamespace(id=uuid.UUID("5d2e9b34-0000-4000-8000-000000000000"), items_count=items)


def _check(engine: FakeEngine, redis: FakeRedis) -> dict[str, Any]:
    result = HealthChecker(engine, redis).check().model_dump()  # type: ignore[arg-type]
    assert_matches_schema("Health", result)
    return result


def test_health_ready_when_everything_is_up(fake_redis: FakeRedis) -> None:
    write_status(fake_redis, STUB_STATUS, 30)
    result = _check(FakeEngine(_catalog(41_280)), fake_redis)
    assert result["ready"] is True
    assert result["components"]["catalog"] == {"ok": True, "detail": "версия 5d2e9b34, 41 280 позиций"}
    assert result["components"]["models"]["ok"] is True


def test_health_without_ml_worker(fake_redis: FakeRedis) -> None:
    result = _check(FakeEngine(_catalog()), fake_redis)
    assert result["ready"] is False
    assert result["components"]["models"] == {"ok": False, "detail": "ML-воркер не запущен или ещё загружает модели."}
    assert result["components"]["catalog"]["ok"] is True


@pytest.mark.parametrize(
    ("status", "detail"),
    [
        ({**STUB_STATUS, "models": False}, "Модели ещё загружаются."),
        ({**STUB_STATUS, "models": False, "error": "Не удалось загрузить модели."}, "Не удалось загрузить модели."),
    ],
)
def test_health_models_not_ready(fake_redis: FakeRedis, status: dict[str, Any], detail: str) -> None:
    write_status(fake_redis, status, 30)
    result = _check(FakeEngine(_catalog()), fake_redis)
    assert result["ready"] is False
    assert result["components"]["models"] == {"ok": False, "detail": detail}


def test_health_explains_catalog_fallback(fake_redis: FakeRedis) -> None:
    """Каталог не скачался — система работает на заглушке, а /health говорит почему (без правки контракта)."""
    write_status(fake_redis, {**STUB_STATUS, "fallback": CATALOG_FALLBACK}, 30)
    result = _check(FakeEngine(_catalog()), fake_redis)
    assert result["ready"] is True
    assert result["components"]["models"] == {"ok": True, "detail": CATALOG_FALLBACK}


def test_health_ml_catalog_not_loaded(fake_redis: FakeRedis) -> None:
    write_status(fake_redis, {**STUB_STATUS, "catalog": False}, 30)
    result = _check(FakeEngine(_catalog()), fake_redis)
    assert result["components"]["catalog"] == {"ok": False, "detail": "ML-воркер ещё не загрузил каталог."}


def test_health_database_down(fake_redis: FakeRedis) -> None:
    write_status(fake_redis, STUB_STATUS, 30)
    result = _check(FakeEngine(broken=True), fake_redis)
    assert result["ready"] is False
    assert result["components"]["database"] == {"ok": False, "detail": "Нет связи с базой данных."}
    assert result["components"]["catalog"]["ok"] is False


def test_health_redis_down(fake_redis: FakeRedis) -> None:
    fake_redis.broken = True
    result = _check(FakeEngine(_catalog()), fake_redis)
    assert result["ready"] is False
    assert result["components"]["redis"] == {"ok": False, "detail": "Нет связи с Redis."}
    assert result["components"]["models"]["ok"] is False


@pytest.mark.parametrize(("row", "detail"), [(None, "Каталог КТРУ не загружен."), (_catalog(0), "Каталог КТРУ пуст.")])
def test_health_catalog_missing(fake_redis: FakeRedis, row: Any, detail: str) -> None:
    write_status(fake_redis, STUB_STATUS, 30)
    result = _check(FakeEngine(row), fake_redis)
    assert result["components"]["catalog"] == {"ok": False, "detail": detail}


@pytest.fixture
def api(settings: Settings) -> Iterator[tuple[TestClient, dict[str, Any]]]:
    app = create_app(settings)
    state: dict[str, Any] = {"ml": None}
    app.dependency_overrides[deps.get_ml_status] = lambda: state["ml"]
    app.dependency_overrides[deps.get_health_checker] = lambda: HealthChecker(FakeEngine(_catalog()), FakeRedis())
    with TestClient(app) as client:
        yield client, state


def test_health_endpoint(api: tuple[TestClient, dict[str, Any]]) -> None:
    client, _ = api
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert_matches_schema("Health", response.json())


def test_config_endpoint_matches_contract(api: tuple[TestClient, dict[str, Any]]) -> None:
    client, state = api
    state["ml"] = {
        **STUB_STATUS,
        "mode": {"extractor": "hybrid", "reranker": "cross_encoder", "device": "cpu", "llm_enabled": True},
    }
    body = client.get("/api/v1/config").json()
    assert_matches_schema("PublicConfig", body)
    assert body == {
        "limits": {"max_file_mb": 50, "max_rows": 5000, "max_items_per_job": 1000, "classify_max_items": 20},
        "thresholds": {"green": 0.70, "yellow": 0.5},
        "mode": {"extractor": "hybrid", "reranker": "cross_encoder", "device": "cpu", "llm_enabled": True},
    }


def test_config_without_ml_worker_uses_local_mode(api: tuple[TestClient, dict[str, Any]]) -> None:
    client, state = api
    state["ml"] = None
    assert client.get("/api/v1/config").json()["mode"] == STUB_STATUS["mode"]
    state["ml"] = {**STUB_STATUS, "mode": {"extractor": "magic"}}  # испорченный режим не роняет /config
    assert client.get("/api/v1/config").json()["mode"] == STUB_STATUS["mode"]
