"""Формат ошибок, request_id, заголовки безопасности, лимит тела и CORS."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Annotated

import pytest
from fastapi import Body, FastAPI, Query, Request
from fastapi.testclient import TestClient

from backend.app.errors import ApiError, plural, validation_message
from backend.app.main import create_app
from backend.core.settings import Settings

from .openapi import assert_matches_schema


def _app(settings: Settings) -> FastAPI:
    app = create_app(settings)

    @app.get("/api/v1/_test/items")
    def items(page: Annotated[int, Query(ge=1)] = 1) -> dict[str, int]:
        return {"page": page}

    @app.post("/api/v1/_test/classify")
    def classify(texts: Annotated[list[str], Body(min_length=1, max_length=20, embed=True)]) -> dict[str, int]:
        return {"n": len(texts)}

    @app.post("/api/v1/_test/raw")
    async def raw(request: Request) -> dict[str, int]:
        return {"n": len(await request.body())}

    @app.get("/api/v1/_test/conflict")
    def conflict() -> None:
        raise ApiError(409, "invalid_state", "Задача уже завершена.", {"status": "done"})

    @app.get("/api/v1/_test/boom")
    def boom() -> None:
        raise RuntimeError("секрет: пароль=123")

    return app


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    with TestClient(_app(settings), raise_server_exceptions=False) as c:
        yield c


def _error(response) -> dict:
    body = response.json()
    assert_matches_schema("Error", body)
    return body["error"]


def test_validation_error_is_400_in_our_format(client: TestClient) -> None:
    response = client.get("/api/v1/_test/items", params={"page": 0})
    assert response.status_code == 400
    error = _error(response)
    assert error == {
        "code": "invalid_request",
        "message": "Некорректный запрос: поле page должно быть не меньше 1.",
        "details": {"field": "page"},
    }


def test_validation_of_body_lists(client: TestClient) -> None:
    response = client.post("/api/v1/_test/classify", json={"texts": []})
    assert response.status_code == 400
    assert _error(response)["message"] == "Некорректный запрос: поле texts должно содержать не меньше 1 элемента."
    response = client.post("/api/v1/_test/classify", content=b"{not json", headers={"Content-Type": "application/json"})
    assert response.status_code == 400
    assert _error(response)["code"] == "invalid_request"


def test_unknown_route_and_method(client: TestClient) -> None:
    response = client.get("/api/v1/nope")
    assert response.status_code == 404
    assert _error(response) == {"code": "not_found", "message": "Адрес не найден.", "details": {"object": "route"}}
    response = client.delete("/api/v1/_test/items")
    assert response.status_code == 405
    assert _error(response)["code"] == "invalid_request"
    assert "GET" in response.headers["allow"]


def test_api_error_keeps_status_and_details(client: TestClient) -> None:
    response = client.get("/api/v1/_test/conflict")
    assert response.status_code == 409
    assert _error(response) == {
        "code": "invalid_state",
        "message": "Задача уже завершена.",
        "details": {"status": "done"},
    }


def test_unhandled_error_is_500_without_internals(client: TestClient) -> None:
    response = client.get("/api/v1/_test/boom", headers={"X-Request-ID": "req-42"})
    assert response.status_code == 500
    error = _error(response)
    assert error["code"] == "internal_error"
    assert error["details"] == {"request_id": "req-42"}
    assert "пароль" not in response.text
    assert "RuntimeError" not in response.text
    assert response.headers["x-request-id"] == "req-42"


def test_request_id_is_generated_when_missing_or_unsafe(client: TestClient) -> None:
    generated = client.get("/api/v1/_test/items").headers["x-request-id"]
    assert len(generated) == 32
    for unsafe in ("a" * 65, "id with spaces", "<script>"):
        echoed = client.get("/api/v1/_test/items", headers={"X-Request-ID": unsafe}).headers["x-request-id"]
        assert echoed != unsafe
        assert len(echoed) == 32


def test_security_headers(client: TestClient) -> None:
    headers = client.get("/api/v1/_test/items").headers
    assert headers["x-content-type-options"] == "nosniff"
    assert headers["x-frame-options"] == "DENY"
    assert headers["referrer-policy"] == "no-referrer"
    assert headers["cache-control"] == "no-store"
    assert "server" not in headers


def test_body_limit_by_content_length(client: TestClient) -> None:
    too_big = str(51 * 1024 * 1024 + 1)
    response = client.post("/api/v1/_test/raw", content=b"x", headers={"Content-Length": too_big})
    assert response.status_code == 413
    assert _error(response)["code"] == "file_too_large"
    response = client.post("/api/v1/_test/raw", content=b"x", headers={"Content-Length": "abc"})
    assert response.status_code == 400


def test_body_limit_without_content_length(settings: Settings) -> None:
    small = settings.model_copy(update={"max_file_mb": 1})
    chunk = b"x" * (256 * 1024)

    def stream() -> Iterator[bytes]:
        for _ in range(12):  # 3 МБ при лимите 1 МБ + 1 МБ на служебные части
            yield chunk

    with TestClient(_app(small), raise_server_exceptions=False) as c:
        raw = c.post("/api/v1/_test/raw", content=stream())
        assert raw.status_code == 413, raw.text
        assert _error(raw)["message"] == "Файл больше 1 МБ. Разбейте его на части."
        parsed = c.post("/api/v1/_test/classify", content=stream(), headers={"Content-Type": "application/json"})
        assert parsed.status_code == 413, parsed.text
        assert c.post("/api/v1/_test/raw", content=b"x" * 1024).json() == {"n": 1024}


def test_cors(client: TestClient) -> None:
    allowed = client.options(
        "/api/v1/_test/items",
        headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "GET"},
    )
    assert allowed.status_code == 200
    assert allowed.headers["access-control-allow-origin"] == "http://localhost:5173"
    simple = client.get("/api/v1/_test/items", headers={"Origin": "http://localhost:5173"})
    exposed = simple.headers["access-control-expose-headers"].lower()
    assert "x-unreviewed-count" in exposed
    assert "content-disposition" in exposed
    foreign = client.get("/api/v1/_test/items", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in foreign.headers


def test_validation_message_fallbacks() -> None:
    assert validation_message([]) == ("Некорректный запрос.", None)
    message, field = validation_message([{"type": "missing", "loc": ("body",)}])
    assert (message, field) == ("Некорректный запрос: тело запроса обязательно.", None)
    message, _ = validation_message(
        [{"type": "literal_error", "loc": ("query", "format"), "ctx": {"expected": "'a' or 'b'"}}]
    )
    assert message == "Некорректный запрос: поле format должно быть одним из: 'a', 'b'."


@pytest.mark.parametrize(
    ("n", "word"),
    [(1, "позиция"), (2, "позиции"), (5, "позиций"), (11, "позиций"), (21, "позиция"), (41_280, "позиций")],
)
def test_plural(n: int, word: str) -> None:
    assert plural(n, "позиция", "позиции", "позиций") == word


def test_body_limit_is_larger_for_catalog_import(settings: Settings) -> None:
    """Каталог КТРУ больше спецификации: у POST /catalog/import свой лимит (CATALOG_IMPORT_MAX_MB)."""
    small = settings.model_copy(update={"max_file_mb": 1, "catalog_import_max_mb": 5})
    three_mb = str(3 * 1024 * 1024)
    with TestClient(_app(small), raise_server_exceptions=False) as c:
        files = c.post("/api/v1/files", content=b"x", headers={"Content-Length": three_mb})
        assert (files.status_code, _error(files)["message"]) == (413, "Файл больше 1 МБ. Разбейте его на части.")
        too_big = str(7 * 1024 * 1024)
        catalog = c.post("/api/v1/catalog/import", content=b"x", headers={"Content-Length": too_big})
        assert (catalog.status_code, _error(catalog)["message"]) == (413, "Файл больше 5 МБ. Разбейте его на части.")
        within = c.post("/api/v1/catalog/import", content=b"x", headers={"Content-Length": three_mb})
        assert within.status_code != 413
