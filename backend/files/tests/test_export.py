"""Тесты модуля экспорта результатов (FR-7.1, FR-7.2)."""

import io
import json
import openpyxl
import pytest

from backend.files.exceptions import UnreviewedItemsError
from backend.files.export import (
    ADDED_COLUMNS,
    escape_formula_for_csv,
    export_csv,
    export_job_results,
    export_json,
    export_xlsx,
    format_content_disposition,
)
from backend.files.schemas import ExportFormat, ExportItem


@pytest.fixture
def sample_export_items():
    return [
        ExportItem(
            row_index=0,
            raw_row=["Ноутбук Lenovo IdeaPad 3", "12", "шт"],
            ktru_code="26.20.11.110-00000001",
            ktru_name="Компьютер портативный (ноутбук)",
            confidence=0.942,
            status="confirmed",
            source="user",
            flags=[],
        ),
        ExportItem(
            row_index=1,
            raw_row=["Принтер Xerox B230", "3", "шт"],
            ktru_code="26.20.16.120-00000002",
            ktru_name="Принтер лазерный",
            confidence=0.875,
            status="processed",
            source="prediction",
            flags=["low_extraction_confidence"],
        ),
        ExportItem(
            row_index=2,
            raw_row=["Непонятная позиция", "1", "компл"],
            ktru_code=None,
            ktru_name=None,
            confidence=None,
            status="failed",
            source=None,
            flags=[],
            error="Не удалось извлечь атрибуты",
        ),
        ExportItem(
            row_index=3,
            raw_row=["Отменённая строка", "5", "шт"],
            ktru_code=None,
            ktru_name=None,
            confidence=None,
            status="pending",
            source=None,
            flags=[],
        ),
    ]


@pytest.fixture
def sample_headers():
    return ["Наименование", "Кол-во", "Ед. изм."]


def test_escape_formula_for_csv():
    """Проверка экранирования CSV-инъекций (=, +, -, @)."""
    assert escape_formula_for_csv("=1+1") == "'=1+1"
    assert escape_formula_for_csv("+SUM(A1)") == "'+SUM(A1)"
    assert escape_formula_for_csv("-100") == "'-100"
    assert escape_formula_for_csv("@calc") == "'@calc"
    assert escape_formula_for_csv("Обычный текст") == "Обычный текст"
    assert escape_formula_for_csv(123) == "123"
    assert escape_formula_for_csv(None) == ""


def test_format_content_disposition():
    """Проверка формирования заголовка Content-Disposition по RFC 5987 / RFC 6266."""
    header = format_content_disposition("спецификация_2026.xlsx")
    assert 'attachment; filename=' in header
    assert "filename*=UTF-8''" in header
    assert "%D1%81%D0%BF%D0%B5%D1%86%D0%B8%D1%84%D0%B8%D0%BA%D0%B0%D1%86%D0%B8%D1%8F" in header


def test_export_csv_structure_and_bom(sample_headers, sample_export_items):
    """Проверка структуры CSV, экранирования формул и UTF-8 BOM."""
    csv_bytes = export_csv(sample_headers, sample_export_items, delimiter=";")
    assert csv_bytes.startswith(b"\xef\xbb\xbf")  # UTF-8 BOM

    text = csv_bytes.decode("utf-8-sig")
    lines = text.strip().split("\r\n")
    if len(lines) == 1:
        lines = text.strip().split("\n")

    # Проверка строки заголовков
    header_line = lines[0].split(";")
    expected_headers = sample_headers + ADDED_COLUMNS
    assert header_line == expected_headers

    # Строка 1: confirmed, user
    row1 = lines[1].split(";")
    assert row1[0] == "Ноутбук Lenovo IdeaPad 3"
    assert row1[3] == "26.20.11.110-00000001"
    assert row1[5] == "94%"
    assert row1[6] == "Подтверждено"
    assert row1[7] == "Пользователь"

    # Строка 2: processed, prediction, flag
    row2 = lines[2].split(";")
    assert row2[5] == "88%"
    assert row2[6] == "Не проверено"
    assert row2[7] == "Модель"
    assert "Низкая уверенность извлечения" in row2[8]

    # Строка 3: failed
    row3 = lines[3].split(";")
    assert row3[3] == ""  # ktru_code пустой
    assert row3[4] == ""  # ktru_name пустое
    assert row3[6] == "Ошибка"

    # Строка 4: pending
    row4 = lines[4].split(";")
    assert row4[6] == "Не обработана"


def test_export_xlsx_string_cells(sample_headers, sample_export_items):
    """Проверка XLSX: открывается, содержит данные и все ячейки строго строкового типа (data_type='s')."""
    # Добавляем потенциально опасную строку с формулой
    items_with_formula = sample_export_items + [
        ExportItem(
            row_index=4,
            raw_row=["=cmd|' /C calc'!A0", "1", "шт"],
            ktru_code="26.20.11.110-00000001",
            ktru_name="Тест",
            confidence=0.9,
            status="confirmed",
        )
    ]

    xlsx_bytes = export_xlsx(sample_headers, items_with_formula)
    assert len(xlsx_bytes) > 0

    wb = openpyxl.load_workbook(io.BytesIO(xlsx_bytes))
    ws = wb.active
    assert ws.title == "Результаты"

    # Проверяем, что все ячейки строки имеют строковый тип (не 'f' формула)
    for row in ws.iter_rows():
        for cell in row:
            assert cell.data_type in ("s", "inlineStr", "str"), f"Ячейка {cell.coordinate} не должна быть формулой, текущий тип: {cell.data_type}"

    # Проверяем опасную формулу — она должна сохраниться как обычный текст со строковым типом
    formula_cell = ws.cell(row=6, column=1)
    assert formula_cell.data_type == "s"
    assert formula_cell.value == "=cmd|' /C calc'!A0"


def test_export_json_format(sample_headers, sample_export_items):
    """Проверка выгрузки в JSON."""
    json_bytes = export_json(sample_headers, sample_export_items)
    data = json.loads(json_bytes.decode("utf-8"))

    assert len(data) == 4
    assert data[0]["Наименование"] == "Ноутбук Lenovo IdeaPad 3"
    assert data[0]["ktru_code"] == "26.20.11.110-00000001"
    assert data[0]["status"] == "confirmed"
    assert data[0]["status_label"] == "Подтверждено"
    assert data[0]["source"] == "user"

    # failed позиция
    assert data[2]["ktru_code"] is None
    assert data[2]["status"] == "failed"
    assert data[2]["status_label"] == "Ошибка"

    # pending позиция
    assert data[3]["status"] == "pending"
    assert data[3]["status_label"] == "Не обработана"


def test_export_job_results_unreviewed_count_and_header(sample_headers, sample_export_items):
    """Проверка подсчёта X-Unreviewed-Count и заголовка."""
    # В sample_export_items одна позиция 'processed'
    res = export_job_results(
        sample_export_items,
        sample_headers,
        export_format=ExportFormat.XLSX,
        original_filename="spec.xlsx",
        allow_unreviewed=True,
    )

    assert res.unreviewed_count == 1
    assert res.filename == "spec_ktru.xlsx"
    assert res.media_type == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    assert "filename*=UTF-8''spec_ktru.xlsx" in res.content_disposition


def test_export_job_results_allow_unreviewed_false(sample_headers, sample_export_items):
    """При allow_unreviewed=False и наличии непроверенных строк генерируется UnreviewedItemsError (409)."""
    with pytest.raises(UnreviewedItemsError) as exc_info:
        export_job_results(
            sample_export_items,
            sample_headers,
            export_format=ExportFormat.CSV,
            allow_unreviewed=False,
        )

    assert exc_info.value.code == "unreviewed_items"
    assert exc_info.value.details == {"unreviewed": 1}
    assert "Осталось 1 непроверенных" in str(exc_info.value)


def test_export_job_results_format_source(sample_headers, sample_export_items):
    """Проверка автоматического выбора формата при format='source'."""
    # Для CSV -> CSV
    res_csv = export_job_results(
        sample_export_items,
        sample_headers,
        export_format=ExportFormat.SOURCE,
        original_filename="data.csv",
    )
    assert res_csv.filename.endswith(".csv")
    assert "text/csv" in res_csv.media_type

    # Для XLSX -> XLSX
    res_xlsx = export_job_results(
        sample_export_items,
        sample_headers,
        export_format=ExportFormat.SOURCE,
        original_filename="data.xlsx",
    )
    assert res_xlsx.filename.endswith(".xlsx")
    assert "spreadsheetml" in res_xlsx.media_type

    # Для PDF -> XLSX (по контракту)
    res_pdf = export_job_results(
        sample_export_items,
        sample_headers,
        export_format=ExportFormat.SOURCE,
        original_filename="document.pdf",
    )
    assert res_pdf.filename.endswith(".xlsx")
    assert "spreadsheetml" in res_pdf.media_type
