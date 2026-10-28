"""Каталог для ручной правки: GET /catalog/versions, /catalog/items/{code}, /catalog/search (FR-6.2)."""

from __future__ import annotations

import json
import threading
import uuid
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import func, update
from sqlalchemy.orm import Session

from backend.app.routes.catalog import catalog_item
from backend.core import catalog, models
from contracts.stub_pipeline import StubPipeline

from .conftest import Stack
from .openapi import assert_matches_schema


def test_catalog_item_tolerates_loose_characteristics() -> None:
    """CatalogRow.characteristics контракт не проверяет — карточка не должна падать на необычных значениях."""
    row = models.CatalogItem(
        ktru_code="11.11.11.111-00000001",
        okpd2_code="11.11.11.111",
        name="Стол",
        characteristics=[
            {"name": "Длина", "values": [120, "140"], "required": True},
            {"name": "Цвет", "values": "белый"},
            {"values": ["без названия"]},
            "не объект",
        ],
    )
    item = catalog_item(row).model_dump()
    assert_matches_schema("CatalogItem", item)
    assert item["characteristics"] == [
        {"name": "Длина", "values": ["120", "140"], "required": True},
        {"name": "Цвет", "values": ["белый"], "required": False},
    ]


def _search(stack: Stack, **params: Any) -> list[str]:
    response = stack.client.get("/api/v1/catalog/search", params=params)
    assert response.status_code == 200, response.text
    items = response.json()["items"]
    for item in items:
        assert_matches_schema("CatalogItem", item)
    return [item["ktru_code"] for item in items]


def _error(response: Any, status: int, code: str) -> dict[str, Any]:
    assert response.status_code == status, response.text
    assert response.json()["error"]["code"] == code
    return response.json()["error"]


@pytest.mark.db
def test_versions_and_catalog_item(stack: Stack) -> None:
    versions = stack.client.get("/api/v1/catalog/versions").json()["items"]
    assert len(versions) == 1
    assert_matches_schema("CatalogVersion", versions[0])
    assert (versions[0]["status"], versions[0]["items_count"]) == ("ready", 8)

    card = stack.client.get("/api/v1/catalog/items/99.99.01.000-00000001").json()
    assert_matches_schema("CatalogItem", card)
    assert (card["name"], card["unit"]) == ("Ноутбук", "шт")
    assert card["characteristics"][0] == {"name": "Диагональ экрана", "values": ['от 13"'], "required": True}
    version_id = versions[0]["version_id"]
    explicit = stack.client.get("/api/v1/catalog/items/99.99.01.000-00000001", params={"version_id": version_id})
    assert explicit.json() == card

    error = _error(stack.client.get("/api/v1/catalog/items/00.00.00.000-00000000"), 404, "not_found")
    assert error["details"] == {"object": "ktru_code"}
    missing_version = stack.client.get(
        "/api/v1/catalog/items/99.99.01.000-00000001", params={"version_id": str(uuid.uuid4())}
    )
    assert _error(missing_version, 404, "not_found")["details"] == {"object": "catalog_version"}


@pytest.mark.db
def test_search_by_code_and_name(stack: Stack) -> None:
    assert _search(stack, q="99.99.03") == ["99.99.03.000-00000001", "99.99.03.000-00000002"]  # начало кода
    assert sorted(_search(stack, q="принтер")) == ["99.99.03.000-00000001", "99.99.03.000-00000002"]
    assert _search(stack, q="ЛАЗЕР") == ["99.99.03.000-00000001"]  # часть слова, без учёта регистра
    assert sorted(_search(stack, q="ноутбуки")) == ["99.99.01.000-00000001", "99.99.01.000-00000002"]  # форма слова
    assert _search(stack, q="Ноутбук", limit=1) == ["99.99.01.000-00000001"]  # точнее совпадение — выше
    assert _search(stack, q="99%") == []  # % — обычный символ
    assert _search(stack, q="холодильник") == []

    for q in ("а", "  а  "):
        _error(stack.client.get("/api/v1/catalog/search", params={"q": q}), 400, "invalid_request")
    _error(stack.client.get("/api/v1/catalog/search"), 400, "invalid_request")
    _error(stack.client.get("/api/v1/catalog/search", params={"q": "ноутбук", "limit": 51}), 400, "invalid_request")


@pytest.mark.db
def test_search_skips_enlarged_items(stack: Stack) -> None:
    """Укрупнённая позиция (is_enlarged) хранится и открывается по коду, но в подсказках для правки её нет."""
    with Session(stack.engine) as session, session.begin():
        session.execute(
            update(models.CatalogItem)
            .where(models.CatalogItem.ktru_code == "99.99.03.000-00000002")
            .values(is_enlarged=True)
        )
    assert _search(stack, q="принтер") == ["99.99.03.000-00000001"]
    assert stack.client.get("/api/v1/catalog/items/99.99.03.000-00000002").status_code == 200


# --- POST /catalog/import (FR-8.1) ------------------------------------------------------------------------------

NEW_ROWS = [
    {"ktru_code": "11.11.11.111-00000001", "okpd2_code": "11.11.11.111", "name": "Стол письменный"},
    {"ktru_code": "11.11.11.111-00000002", "okpd2_code": "11.11.11.111", "name": "Стол", "is_enlarged": True},
]


def _jsonl(rows: list[dict[str, Any]]) -> bytes:
    return ("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n").encode()


def _import(stack: Stack, content: bytes, note: str | None = None) -> Any:
    data = {"note": note} if note else None
    return stack.client.post("/api/v1/catalog/import", files={"file": ("ktru.jsonl", content)}, data=data)


class LoadingPipeline:
    """Конвейер ML с load_version (CatalogVersions из ml/pipeline.py): запоминает, что ему передали."""

    def __init__(self, fail: bool = False) -> None:
        self.versions: dict[str, list[dict[str, Any]]] = {"": []}
        self.fail = fail

    def load_version(self, version_id: str, rows: list[dict[str, Any]]) -> int:
        if self.fail:
            raise MemoryError
        self.versions[version_id] = rows
        return len(rows)

    def loaded_versions(self) -> list[str]:
        return list(self.versions)


def _version(stack: Stack, version_id: str) -> dict[str, Any]:
    versions = stack.client.get("/api/v1/catalog/versions").json()["items"]
    return next(v for v in versions if v["version_id"] == version_id)


def _sync(stack: Stack, pipeline: Any) -> None:
    sync = catalog.CatalogSync(stack.engine, pipeline)
    sync.poll()
    for thread in threading.enumerate():
        if thread.name == "catalog-load":
            thread.join(timeout=10)


@pytest.mark.db
def test_catalog_import_end_to_end(stack: Stack) -> None:
    response = _import(stack, _jsonl(NEW_ROWS), note="КТРУ на 29.10")
    assert response.status_code == 202, response.text
    version = response.json()
    assert_matches_schema("CatalogVersion", version)
    assert (version["status"], version["source"], version["note"]) == ("importing", "api:ktru.jsonl", "КТРУ на 29.10")
    [(version_id, storage_path)] = stack.queue.catalog_imports
    path = stack.settings.upload_dir / storage_path
    assert path.is_file()

    catalog.run_catalog_import(stack.engine, uuid.UUID(version_id), path)  # worker-io
    assert not path.exists()
    assert (_version(stack, version_id)["status"], _version(stack, version_id)["items_count"]) == ("importing", 2)
    old = stack.client.get("/api/v1/catalog/search", params={"q": "ноутбук"}).json()["items"]
    assert old, "пока ML не построил индекс, работает прежняя версия"

    pipeline = LoadingPipeline()
    _sync(stack, pipeline)  # worker-ml
    assert _version(stack, version_id)["status"] == "ready"
    rows = pipeline.versions[version_id]
    assert sorted((r["ktru_code"], r["is_enlarged"]) for r in rows) == [
        ("11.11.11.111-00000001", False),
        ("11.11.11.111-00000002", True),
    ]
    assert _search(stack, q="стол") == ["11.11.11.111-00000001"]  # новые задачи и поиск — на новой версии
    _sync(stack, pipeline)  # уже загружена — второй раз не грузим
    assert list(pipeline.versions) == ["", version_id]


@pytest.mark.db
def test_catalog_import_failures(stack: Stack) -> None:
    error = _error(_import(stack, b"not json\n"), 422, "file_unreadable")
    assert "CatalogRow" in error["message"]

    _import(stack, _jsonl(NEW_ROWS[:1]) + b'{"ktru_code": "x"}\n')
    [(bad_id, bad_path)] = stack.queue.catalog_imports
    catalog.run_catalog_import(stack.engine, uuid.UUID(bad_id), stack.settings.upload_dir / bad_path)
    bad = _version(stack, bad_id)
    assert bad["status"] == "failed"
    assert bad["note"].startswith("Ошибка: Строка 2")

    _import(stack, _jsonl(NEW_ROWS))
    version_id, path = stack.queue.catalog_imports[-1]
    catalog.run_catalog_import(stack.engine, uuid.UUID(version_id), stack.settings.upload_dir / path)
    _sync(stack, LoadingPipeline(fail=True))  # ML не смог построить индекс (например, не хватило памяти)
    assert _version(stack, version_id)["status"] == "failed"


@pytest.mark.db
def test_catalog_import_with_stub_and_stale(stack: Stack) -> None:
    _import(stack, _jsonl(NEW_ROWS))
    version_id, path = stack.queue.catalog_imports[-1]
    catalog.run_catalog_import(stack.engine, uuid.UUID(version_id), stack.settings.upload_dir / path)
    _sync(stack, StubPipeline())  # заглушке индекс не нужен — версия сразу ready
    assert _version(stack, version_id)["status"] == "ready"

    _import(stack, _jsonl(NEW_ROWS))
    stale_id = stack.queue.catalog_imports[-1][0]
    with Session(stack.engine) as session, session.begin():
        old = func.now() - timedelta(hours=3)
        session.execute(
            update(models.CatalogVersion).where(models.CatalogVersion.id == uuid.UUID(stale_id)).values(loaded_at=old)
        )
    assert catalog.fail_stale_imports(stack.engine) == 1
    assert _version(stack, stale_id)["status"] == "failed"
