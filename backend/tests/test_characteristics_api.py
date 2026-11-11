"""Дополнительная задача B: итоговые характеристики, ввод и единые счётчики."""

from __future__ import annotations

import io
import uuid
from importlib import import_module
from typing import Any

import openpyxl
import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from backend.app.routes.export import _export_items
from backend.core import models
from backend.files.schemas import ExportResult

from .conftest import HEADER, MAPPING, ROWS, Stack, xlsx_bytes
from .openapi import assert_matches_schema

pytestmark = pytest.mark.db

LAPTOP = "99.99.01.000-00000001"
MONITOR = "99.99.02.000-00000001"
PAPER = "99.99.04.000-00000001"


@pytest.fixture
def example(stack: Stack) -> tuple[Stack, str, str]:
    job = stack.finished_job(ROWS[:2])
    items = stack.client.get(f"/api/v1/jobs/{job['job_id']}/items").json()["items"]
    item_id = items[0]["item_id"]
    with Session(stack.engine) as session:
        item = session.get(models.Item, uuid.UUID(item_id))
        assert item is not None
        version_id = session.get(models.Job, item.job_id).catalog_version_id  # type: ignore[union-attr]
        laptop = session.scalar(
            select(models.CatalogItem).where(
                models.CatalogItem.version_id == version_id, models.CatalogItem.ktru_code == LAPTOP
            )
        )
        assert laptop is not None
        laptop.characteristics = [
            {"name": "ОЗУ", "values": ["от 8 ГБ"], "required": True},
            {"name": "Цвет", "values": ["серый", "чёрный"], "required": False},
            {"name": "Накопитель", "values": ["SSD"], "required": True},
            {"name": "Материал", "values": ["пластик"], "required": False},
            {"name": "Экран", "values": ["15 дюймов"], "required": True},
        ]
        predictions = session.scalars(
            select(models.Prediction).where(models.Prediction.item_id == item.id).order_by(models.Prediction.rank)
        ).all()
        assert len(predictions) == 3
        predictions[0].ktru_code = LAPTOP
        predictions[0].characteristic_matches = [
            {"name": "ОЗУ", "expected": "от 8 ГБ", "found": "16 ГБ", "status": "match", "required": True},
            {"name": "Цвет", "expected": "серый; чёрный", "found": "серый", "status": "match", "required": False},
            {"name": "Экран", "expected": "15 дюймов", "found": "13 дюймов", "status": "mismatch", "required": True},
        ]
        predictions[1].ktru_code = MONITOR
        predictions[1].characteristic_matches = [
            {
                "name": "Диагональ экрана",
                "expected": "от 21 дюйма",
                "found": None,
                "status": "missing",
                "required": True,
            }
        ]
        predictions[2].ktru_code = LAPTOP
        session.commit()
    return stack, job["job_id"], item_id


def _get(stack: Stack, item_id: str) -> dict[str, Any]:
    response = stack.client.get(f"/api/v1/items/{item_id}/characteristics")
    assert response.status_code == 200, response.text
    assert_matches_schema("ItemCharacteristics", response.json())
    return response.json()


def _put(stack: Stack, item_id: str, code: str, values: dict[str, Any]) -> dict[str, Any]:
    response = stack.client.put(f"/api/v1/items/{item_id}/characteristics", json={"ktru_code": code, "values": values})
    assert response.status_code == 200, response.text
    assert_matches_schema("ItemCharacteristics", response.json())
    return response.json()


def _chars(result: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {char["name"]: char for char in result["characteristics"]}


def test_prediction_required_optional_and_counts(example: tuple[Stack, str, str]) -> None:
    stack, job_id, item_id = example
    result = _get(stack, item_id)
    assert (result["ktru_code"], result["source"], result["missing_required_count"]) == (LAPTOP, "prediction", 1)
    assert [char["name"] for char in result["characteristics"]] == ["ОЗУ", "Накопитель", "Экран", "Цвет"]
    chars = _chars(result)
    assert (chars["ОЗУ"]["found"], chars["ОЗУ"]["status"]) == ("16 ГБ", "match")
    assert (chars["Накопитель"]["allowed_values"], chars["Накопитель"]["status"]) == (["SSD"], "missing")
    assert chars["Экран"]["status"] == "mismatch"
    assert "Материал" not in chars

    item = stack.client.get(f"/api/v1/items/{item_id}").json()
    assert_matches_schema("Item", item)
    assert item["missing_required_count"] == 1
    assert stack.client.get(f"/api/v1/jobs/{job_id}").json()["summary"]["missing_required"] == 1
    page = stack.client.get(f"/api/v1/jobs/{job_id}/items", params={"missing_required": "true"}).json()
    assert (page["total"], [row["item_id"] for row in page["items"]]) == (1, [item_id])
    assert stack.client.get(f"/api/v1/jobs/{job_id}/items", params={"missing_required": "false"}).json()["total"] == 2


def test_fill_trim_clear_optional_and_history(example: tuple[Stack, str, str]) -> None:
    stack, job_id, item_id = example
    result = _put(stack, item_id, LAPTOP, {"Накопитель": "  SSD  ", "Материал": " пластик "})
    assert result["missing_required_count"] == 0
    assert (_chars(result)["Накопитель"]["value"], _chars(result)["Накопитель"]["status"]) == ("SSD", "filled")
    assert (_chars(result)["Материал"]["value"], _chars(result)["Материал"]["required"]) == ("пластик", False)
    assert stack.client.get(f"/api/v1/jobs/{job_id}").json()["summary"]["missing_required"] == 0
    assert stack.client.get(f"/api/v1/jobs/{job_id}/items", params={"missing_required": "true"}).json()["total"] == 0

    result = _put(stack, item_id, LAPTOP, {"Накопитель": "   ", "Материал": None})
    assert result["missing_required_count"] == 1
    assert _chars(result)["Накопитель"]["status"] == "missing"
    # Сброс (NULL) возвращает пропуск и в облегчённом счётчике сводки/фильтра, а не только в карточке.
    assert stack.client.get(f"/api/v1/jobs/{job_id}").json()["summary"]["missing_required"] == 1
    assert stack.client.get(f"/api/v1/jobs/{job_id}/items", params={"missing_required": "true"}).json()["total"] == 1
    assert "Материал" not in _chars(result)
    _put(stack, item_id, LAPTOP, {"Накопитель": "SSD"})
    result = _put(stack, item_id, LAPTOP, {"Накопитель": ""})
    assert _chars(result)["Накопитель"]["value"] is None
    with Session(stack.engine) as session:
        values = session.scalars(
            select(models.ItemCharacteristicValue.value)
            .where(models.ItemCharacteristicValue.item_id == uuid.UUID(item_id))
            .order_by(models.ItemCharacteristicValue.created_at)
        ).all()
    assert values == ["SSD", "пластик", None, None, "SSD", None]


def test_switch_code_and_restore_old_values(example: tuple[Stack, str, str]) -> None:
    stack, _, item_id = example
    _put(stack, item_id, LAPTOP, {"Накопитель": "SSD"})
    decision = f"/api/v1/items/{item_id}/decision"
    assert stack.client.put(decision, json={"ktru_code": MONITOR}).status_code == 200
    monitor = _get(stack, item_id)
    assert (monitor["source"], monitor["missing_required_count"]) == ("prediction", 1)
    assert _chars(monitor)["Диагональ экрана"]["status"] == "missing"
    _put(stack, item_id, MONITOR, {"Диагональ экрана": "22 дюйма"})
    assert stack.client.put(decision, json={"ktru_code": PAPER}).status_code == 200
    paper = _get(stack, item_id)
    assert (paper["source"], paper["characteristics"], paper["missing_required_count"]) == ("catalog", [], 0)
    assert stack.client.put(decision, json={"ktru_code": LAPTOP}).status_code == 200
    restored = _get(stack, item_id)
    assert (_chars(restored)["Накопитель"]["value"], restored["missing_required_count"]) == ("SSD", 0)


def test_errors_and_atomicity(example: tuple[Stack, str, str]) -> None:
    stack, _, item_id = example
    url = f"/api/v1/items/{item_id}/characteristics"
    for body, status, code in (
        ({"ktru_code": MONITOR, "values": {"ОЗУ": "16 ГБ"}}, 409, "invalid_state"),
        ({"ktru_code": LAPTOP, "values": {"ОЗУ": "16 ГБ", "Чужое поле": "x"}}, 400, "invalid_request"),
        ({"ktru_code": LAPTOP, "values": {}}, 400, "invalid_request"),
        ({"ktru_code": LAPTOP, "values": {"ОЗУ": "x" * 501}}, 400, "invalid_request"),
    ):
        response = stack.client.put(url, json=body)
        assert (response.status_code, response.json()["error"]["code"]) == (status, code), response.text
        assert_matches_schema("Error", response.json())
        if "Чужое поле" in body["values"]:
            assert response.json()["error"]["details"] == {"field": "Чужое поле"}
    assert _chars(_get(stack, item_id))["ОЗУ"]["value"] is None
    missing = stack.client.get(f"/api/v1/items/{uuid.uuid4()}/characteristics")
    assert (missing.status_code, missing.json()["error"]["code"]) == (404, "not_found")


def test_pending_item_has_no_code(stack: Stack) -> None:
    job = stack.create_job(stack.upload(xlsx_bytes([HEADER, ROWS[0]]))["file_id"], mapping=MAPPING)
    stack.parse_only(job["job_id"])
    item = stack.client.get(f"/api/v1/jobs/{job['job_id']}/items").json()["items"][0]
    result = _get(stack, item["item_id"])
    assert (result["ktru_code"], result["characteristics"], result["missing_required_count"]) == (None, [], None)
    response = stack.client.put(
        f"/api/v1/items/{item['item_id']}/characteristics",
        json={"ktru_code": LAPTOP, "values": {"ОЗУ": "16 ГБ"}},
    )
    assert (response.status_code, response.json()["error"]["code"]) == (409, "invalid_state")


def test_export_uses_the_same_characteristics_and_count(example: tuple[Stack, str, str]) -> None:
    stack, job_id, item_id = example
    with Session(stack.engine) as session:
        job = session.get(models.Job, uuid.UUID(job_id))
        assert job is not None
        rows = _export_items(session, job.id, job.column_mapping)
    first = next(row for row in rows if str(row["item_id"]) == item_id)
    assert (first["raw_name"], first["unit"], first["missing_required_count"]) == (ROWS[0][0], "шт", 1)
    assert first["characteristics"] == _get(stack, item_id)["characteristics"]
    response = stack.client.get(f"/api/v1/jobs/{job_id}/export", params={"format": "json"})
    assert (response.status_code, response.headers["x-missing-required-count"]) == (200, "1")
    _put(stack, item_id, LAPTOP, {"Накопитель": "SSD"})
    response = stack.client.get(f"/api/v1/jobs/{job_id}/export", params={"format": "xlsx"})
    assert (response.status_code, response.headers["x-missing-required-count"]) == (200, "0")


def test_manual_code_uses_catalog_characteristics_and_restores_values(example: tuple[Stack, str, str]) -> None:
    stack, job_id, item_id = example
    with Session(stack.engine) as session:
        item = session.get(models.Item, uuid.UUID(item_id))
        assert item is not None
        version_id = session.get(models.Job, item.job_id).catalog_version_id  # type: ignore[union-attr]
        paper = session.scalar(
            select(models.CatalogItem).where(
                models.CatalogItem.version_id == version_id, models.CatalogItem.ktru_code == PAPER
            )
        )
        assert paper is not None
        paper.characteristics = [
            {"name": "Плотность", "values": ["80 г/м2"], "required": True},
            {"name": "Формат", "values": ["A4"], "required": False},
        ]
        session.commit()

    decision = f"/api/v1/items/{item_id}/decision"
    assert stack.client.put(decision, json={"ktru_code": PAPER}).status_code == 200
    result = _get(stack, item_id)
    assert (result["ktru_code"], result["source"], result["missing_required_count"]) == (PAPER, "catalog", 1)
    assert [(char["name"], char["status"], char["found"]) for char in result["characteristics"]] == [
        ("Плотность", "missing", None)
    ]

    result = _put(stack, item_id, PAPER, {"Плотность": "80 г/м2", "Формат": "A4"})
    assert (result["missing_required_count"], result["source"]) == (0, "catalog")
    assert [(char["name"], char["status"], char["value"]) for char in result["characteristics"]] == [
        ("Плотность", "filled", "80 г/м2"),
        ("Формат", "filled", "A4"),
    ]
    assert stack.client.get(f"/api/v1/jobs/{job_id}").json()["summary"]["missing_required"] == 0

    assert stack.client.put(decision, json={"ktru_code": LAPTOP}).status_code == 200
    assert stack.client.put(decision, json={"ktru_code": PAPER}).status_code == 200
    restored = _get(stack, item_id)
    assert _chars(restored)["Плотность"]["value"] == "80 г/м2"
    assert _chars(restored)["Формат"]["value"] == "A4"


def test_filter_combines_with_search_sort_and_pagination(example: tuple[Stack, str, str]) -> None:
    stack, job_id, item_id = example
    page = stack.client.get(
        f"/api/v1/jobs/{job_id}/items",
        params={"missing_required": "true", "sort": "confidence_asc", "page_size": 1},
    ).json()
    assert (page["total"], len(page["items"]), page["items"][0]["item_id"]) == (1, 1, item_id)

    page = stack.client.get(f"/api/v1/jobs/{job_id}/items", params={"missing_required": "true", "q": "Lenovo"}).json()
    assert (page["total"], [row["item_id"] for row in page["items"]]) == (1, [item_id])

    page = stack.client.get(
        f"/api/v1/jobs/{job_id}/items", params={"missing_required": "true", "q": "Lenovo", "page": 2}
    ).json()
    assert (page["total"], page["items"]) == (1, [])


def test_put_characteristics_does_not_change_code_or_status(example: tuple[Stack, str, str]) -> None:
    stack, job_id, item_id = example
    before = stack.client.get(f"/api/v1/items/{item_id}").json()
    _put(stack, item_id, LAPTOP, {"Накопитель": "SSD"})
    after = stack.client.get(f"/api/v1/items/{item_id}").json()
    assert (after["status"], after["final"]) == (before["status"], before["final"])
    summary = stack.client.get(f"/api/v1/jobs/{job_id}").json()["summary"]
    assert (summary["confirmed"], summary["corrected"]) == (0, 0)


def test_every_export_format_has_missing_required_header(example: tuple[Stack, str, str]) -> None:
    stack, job_id, _ = example
    for export_format in ("source", "xlsx", "csv", "json"):
        response = stack.client.get(f"/api/v1/jobs/{job_id}/export", params={"format": export_format})
        assert response.status_code == 200, (export_format, response.text)
        assert response.headers["x-missing-required-count"] == "1", export_format


def test_application_export_builds_xlsx_with_values_and_sources(example: tuple[Stack, str, str]) -> None:
    stack, job_id, item_id = example
    response = stack.client.get(f"/api/v1/jobs/{job_id}/export", params={"format": "application"})
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    assert response.headers["x-missing-required-count"] == "1"
    sheet = openpyxl.load_workbook(io.BytesIO(response.content)).active
    rows = [[cell.value for cell in row] for row in sheet.iter_rows()]
    assert rows[0] == [
        "№ позиции",
        "Исходное наименование",
        "Код КТРУ",
        "Наименование КТРУ",
        "Характеристика",
        "Значение",
        "Единица измерения",
        "Обязательная",
        "Источник значения",
    ]
    position = [row for row in rows[1:] if row[0] == "1"]
    by_name = {row[4]: row for row in position}
    assert [(row[2], row[3], row[6]) for row in position] == [(LAPTOP, "Ноутбук", "шт")] * len(position)
    assert (by_name["ОЗУ"][5], by_name["ОЗУ"][7], by_name["ОЗУ"][8]) == ("16 ГБ", "да", "текст позиции")
    assert (by_name["Накопитель"][5], by_name["Накопитель"][8]) == (None, "не заполнено")
    assert (by_name["Экран"][5], by_name["Экран"][8]) == ("13 дюймов", "текст позиции")
    assert (by_name["Цвет"][5], by_name["Цвет"][7], by_name["Цвет"][8]) == ("серый", "нет", "текст позиции")

    _put(stack, item_id, LAPTOP, {"Накопитель": "SSD"})
    response = stack.client.get(f"/api/v1/jobs/{job_id}/export", params={"format": "application"})
    assert (response.status_code, response.headers["x-missing-required-count"]) == (200, "0")
    sheet = openpyxl.load_workbook(io.BytesIO(response.content)).active
    rows = [[cell.value for cell in row] for row in sheet.iter_rows(min_row=2)]
    filled = next(row for row in rows if row[0] == "1" and row[4] == "Накопитель")
    assert (filled[5], filled[7], filled[8]) == ("SSD", "да", "пользователь")


def test_application_export_keeps_position_without_code(stack: Stack) -> None:
    job = stack.create_job(stack.upload(xlsx_bytes([HEADER, ROWS[0]]))["file_id"], mapping=MAPPING)
    stack.parse_only(job["job_id"])
    assert stack.client.post(f"/api/v1/jobs/{job['job_id']}/cancel").status_code == 200

    response = stack.client.get(f"/api/v1/jobs/{job['job_id']}/export", params={"format": "application"})
    assert (response.status_code, response.headers["x-missing-required-count"]) == (200, "0")
    rows = [
        [cell.value for cell in row]
        for row in openpyxl.load_workbook(io.BytesIO(response.content)).active.iter_rows(min_row=2)
    ]
    assert len(rows) == 1
    assert rows[0][:4] == ["1", ROWS[0][0], None, None]
    assert rows[0][4:] == [None, None, "шт", None, None]


def test_application_export_keeps_code_without_characteristics(example: tuple[Stack, str, str]) -> None:
    stack, job_id, item_id = example
    with Session(stack.engine) as session:
        item = session.get(models.Item, uuid.UUID(item_id))
        assert item is not None
        version_id = session.get(models.Job, item.job_id).catalog_version_id  # type: ignore[union-attr]
        paper = session.scalar(
            select(models.CatalogItem).where(
                models.CatalogItem.version_id == version_id, models.CatalogItem.ktru_code == PAPER
            )
        )
        assert paper is not None
        paper.characteristics = []
        session.commit()
    assert stack.client.put(f"/api/v1/items/{item_id}/decision", json={"ktru_code": PAPER}).status_code == 200

    response = stack.client.get(f"/api/v1/jobs/{job_id}/export", params={"format": "application"})
    assert (response.status_code, response.headers["x-missing-required-count"]) == (200, "0")
    rows = [
        [cell.value for cell in row]
        for row in openpyxl.load_workbook(io.BytesIO(response.content)).active.iter_rows(min_row=2)
        if row[0].value == "1"
    ]
    assert rows == [["1", ROWS[0][0], PAPER, "Бумага офисная", None, None, "шт", None, None]]


def test_application_export_writes_formula_like_values_as_text(example: tuple[Stack, str, str]) -> None:
    stack, job_id, item_id = example
    _put(stack, item_id, LAPTOP, {"Накопитель": "=SUM(A1)"})
    response = stack.client.get(f"/api/v1/jobs/{job_id}/export", params={"format": "application"})
    assert response.status_code == 200, response.text
    sheet = openpyxl.load_workbook(io.BytesIO(response.content)).active
    target = next(row for row in sheet.iter_rows(min_row=2) if row[0].value == "1" and row[4].value == "Накопитель")
    assert (target[5].value, target[5].data_type) == ("=SUM(A1)", "s")


def test_values_are_deleted_with_the_item(example: tuple[Stack, str, str]) -> None:
    stack, _, item_id = example
    _put(stack, item_id, LAPTOP, {"Накопитель": "SSD"})
    with Session(stack.engine) as session:
        session.execute(delete(models.Item).where(models.Item.id == uuid.UUID(item_id)))
        session.commit()
        left = session.scalar(
            select(func.count())
            .select_from(models.ItemCharacteristicValue)
            .where(models.ItemCharacteristicValue.item_id == uuid.UUID(item_id))
        )
    assert left == 0


def test_application_export_passes_ordered_rows_to_file_module(
    example: tuple[Stack, str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    stack, job_id, item_id = example
    captured: dict[str, Any] = {}

    def export_stub(items: list[dict[str, Any]], **kwargs: Any) -> ExportResult:
        captured.update(items=items, kwargs=kwargs)
        return ExportResult(
            content=b"xlsx",
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            filename="application.xlsx",
            unreviewed_count=2,
            content_disposition='attachment; filename="application.xlsx"',
        )

    monkeypatch.setattr(import_module("backend.app.routes.export"), "export_job_results", export_stub)
    response = stack.client.get(f"/api/v1/jobs/{job_id}/export", params={"format": "application"})
    assert (response.status_code, response.headers["x-missing-required-count"]) == (200, "1")
    assert captured["kwargs"]["export_format"] == "application"
    assert len(captured["items"]) == 2
    row = next(row for row in captured["items"] if str(row["item_id"]) == item_id)
    assert [char["name"] for char in row["characteristics"]] == ["ОЗУ", "Накопитель", "Экран", "Цвет"]
    assert (row["raw_name"], row["unit"]) == (ROWS[0][0], "шт")
