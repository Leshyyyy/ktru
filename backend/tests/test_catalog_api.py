"""Каталог для ручной правки: GET /catalog/versions, /catalog/items/{code}, /catalog/search (FR-6.2)."""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from backend.app.routes.catalog import catalog_item
from backend.core import models

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
