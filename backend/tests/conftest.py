from __future__ import annotations

import io
import os
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import openpyxl
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, make_url
from sqlalchemy.engine import URL

from backend import migrate
from backend.app import deps
from backend.app.health import HealthChecker
from backend.app.main import create_app
from backend.core import processing
from backend.core.db import make_engine
from backend.core.queue import ClassifyTimeout
from backend.core.readiness import read_status, write_status
from backend.core.settings import Settings
from contracts.ml_contracts import Pipeline
from contracts.stub_pipeline import StubPipeline

# Состояние worker-ml в Redis, при котором /health готов: заглушка загружена.
STUB_STATUS = {
    "models": True,
    "catalog": True,
    "pipeline": "stub",
    "mode": {"extractor": "rules", "reranker": "none", "device": "cpu", "llm_enabled": False},
    "error": None,
}
# Искусственная спецификация: только выдуманные строки, без данных КТРУ (правило backend/CLAUDE.md).
HEADER = ["Наименование товара", "Характеристики", "Кол-во", "Ед. изм."]
ROWS = [
    ["Ноутбук Lenovo IdeaPad 3 15IAU7", '15.6", Core i5, 16 ГБ', 12, "шт"],
    ["Принтер лазерный Xerox B230", "A4, 34 стр/мин", 3, "шт"],
    ["Бумага офисная А4", "500 листов", 40, "пач"],
]
MAPPING = [
    {"index": 0, "role": "name"},
    {"index": 1, "role": "characteristics"},
    {"index": 2, "role": "quantity"},
    {"index": 3, "role": "unit"},
]


def xlsx_bytes(rows: list[list[Any]], sheet: str = "Лист1") -> bytes:
    workbook = openpyxl.Workbook()
    worksheet = workbook.active
    worksheet.title = sheet
    for row in rows:
        worksheet.append(row)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


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


class FakeQueue:
    """Очередь в памяти: записывает поставленные задачи; broken — брокер недоступен."""

    def __init__(self) -> None:
        self.parse: list[str] = []
        self.chunks: list[tuple[str, list[str]]] = []
        self.broken = False
        # Синхронные задачи (POST /classify) выполняются сразу, как их выполнил бы worker-ml.
        self.engine: Engine | None = None
        self.pipeline: Pipeline = StubPipeline()
        self.classify_timeout = False
        self.quality: list[str] = []
        self.catalog_imports: list[tuple[str, str]] = []

    def _check(self) -> None:
        if self.broken:
            raise ConnectionError("брокер недоступен")

    def parse_file(self, job_id: str) -> None:
        self._check()
        self.parse.append(job_id)

    def process_chunk(self, job_id: str, item_ids: list[str]) -> None:
        self._check()
        self.chunks.append((job_id, item_ids))

    def import_catalog(self, version_id: str, storage_path: str) -> None:
        self._check()
        self.catalog_imports.append((version_id, storage_path))

    def quality_run(self, run_id: str) -> None:
        self._check()
        self.quality.append(run_id)

    def classify(self, texts: list[str], catalog_version_id: str, timeout_s: float) -> list[dict[str, Any]]:
        self._check()
        if self.classify_timeout:
            raise ClassifyTimeout
        assert self.engine is not None, "FakeQueue.engine не задан"
        return processing.classify_texts(self.engine, self.pipeline, texts, uuid.UUID(catalog_version_id))


def drain(queue: FakeQueue, engine: Engine, settings: Settings, pipeline: Pipeline | None = None) -> None:
    """Выполняет задачи из очереди, как worker-io и worker-ml, пока очередь не опустеет."""
    pipeline = pipeline or StubPipeline()
    while queue.parse or queue.chunks:
        if queue.parse:
            processing.parse_job(engine, settings, queue, uuid.UUID(queue.parse.pop(0)))
        else:
            job_id, item_ids = queue.chunks.pop(0)
            processing.process_chunk(engine, settings, pipeline, uuid.UUID(job_id), [uuid.UUID(i) for i in item_ids])


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


# --- API на настоящей базе с очередью в памяти ---------------------------------------------------------------


@dataclass
class Stack:
    client: TestClient
    queue: FakeQueue
    settings: Settings
    engine: Engine  # роль приложения — та же, что у воркеров
    redis: FakeRedis

    def drain(self, pipeline: Pipeline | None = None, settings: Settings | None = None) -> None:
        drain(self.queue, self.engine, settings or self.settings, pipeline)

    def parse_only(self, job_id: str) -> list[tuple[str, list[str]]]:
        """Только разбор файла задачи: поставленные пачки возвращаются, а не выполняются."""
        self.queue.parse.remove(job_id)
        processing.parse_job(self.engine, self.settings, self.queue, uuid.UUID(job_id))
        chunks, self.queue.chunks = self.queue.chunks, []
        return chunks

    def run_chunk(self, chunk: tuple[str, list[str]], pipeline: Pipeline | None = None) -> None:
        job_id, item_ids = chunk
        processing.process_chunk(
            self.engine,
            self.settings,
            pipeline or StubPipeline(),
            uuid.UUID(job_id),
            [uuid.UUID(i) for i in item_ids],
        )

    def upload(self, content: bytes, filename: str = "spec.xlsx") -> dict[str, Any]:
        response = self.client.post("/api/v1/files", files={"file": (filename, content)})
        assert response.status_code == 201, response.text
        return response.json()

    def create_job(self, file_id: str, mapping: list[dict[str, Any]] | None = None, **body: Any) -> dict[str, Any]:
        response = self.client.post(
            "/api/v1/jobs", json={"file_id": file_id, "column_mapping": mapping or MAPPING, **body}
        )
        assert response.status_code == 202, response.text
        return response.json()

    def finished_job(self, rows: list[list[Any]], pipeline: Pipeline | None = None) -> dict[str, Any]:
        """Файл → задача → все задачи очереди выполнены; возвращает задачу из GET /jobs/{id}."""
        job = self.create_job(self.upload(xlsx_bytes([HEADER, *rows]))["file_id"])
        self.drain(pipeline)
        return self.client.get(f"/api/v1/jobs/{job['job_id']}").json()

    def stored_files(self) -> list[Path]:
        return sorted(self.settings.upload_dir.iterdir()) if self.settings.upload_dir.exists() else []


@pytest.fixture
def stack(db_settings: Settings, tmp_path: Path, fake_redis: FakeRedis) -> Iterator[Stack]:
    settings = db_settings.model_copy(update={"upload_dir": tmp_path / "uploads"})
    assert migrate.main(settings) == 0
    write_status(fake_redis, STUB_STATUS, 30)
    engine = make_engine(settings.app_database_url(), application_name="ktru-tests", null_pool=True)
    queue = FakeQueue()
    queue.engine = engine
    app = create_app(settings)
    app.dependency_overrides[deps.get_queue] = lambda: queue
    app.dependency_overrides[deps.get_health_checker] = lambda: HealthChecker(engine, fake_redis)  # type: ignore[arg-type]
    app.dependency_overrides[deps.get_ml_status] = lambda: read_status(fake_redis)
    with TestClient(app) as client:
        yield Stack(client, queue, settings, engine, fake_redis)
    engine.dispose()
