"""Тесты полного разбора строк (parse_rows)."""

import csv
from pathlib import Path
import pytest
import openpyxl

from backend.files.exceptions import (
    FileUnreadableError,
    LimitExceededError,
    MappingInvalidError,
)
from backend.files.parser import parse_rows, validate_column_mapping
from backend.files.schemas import ColumnMappingItem, ColumnRole


def test_validate_column_mapping_success():
    mapping = [
        {"index": 0, "role": "name"},
        {"index": 1, "role": "characteristics"},
        {"index": 2, "role": "quantity"},
        {"index": 3, "role": "unit"},
        {"index": 4, "role": "ignore"},
    ]
    res = validate_column_mapping(mapping)
    assert res[0] == ColumnRole.NAME
    assert res[1] == ColumnRole.CHARACTERISTICS


def test_validate_column_mapping_no_name():
    mapping = [{"index": 0, "role": "quantity"}]
    with pytest.raises(MappingInvalidError) as exc_info:
        validate_column_mapping(mapping)
    assert exc_info.value.code == "mapping_invalid"


def test_validate_column_mapping_multiple_names():
    mapping = [
        {"index": 0, "role": "name"},
        {"index": 1, "role": "name"},
    ]
    with pytest.raises(MappingInvalidError) as exc_info:
        validate_column_mapping(mapping)
    assert exc_info.value.code == "mapping_invalid"


def test_validate_column_mapping_multiple_quantity():
    mapping = [
        {"index": 0, "role": "name"},
        {"index": 1, "role": "quantity"},
        {"index": 2, "role": "quantity"},
    ]
    with pytest.raises(MappingInvalidError) as exc_info:
        validate_column_mapping(mapping)
    assert exc_info.value.code == "mapping_invalid"


def test_parse_rows_concatenation_and_raw_row(tmp_path: Path):
    csv_file = tmp_path / "spec.csv"
    with open(csv_file, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f, delimiter=";")
        writer.writerow(["Код", "Наименование", "Описание", "Параметры", "Кол-во"])
        writer.writerow(["A1", "Ноутбук", "Lenovo IdeaPad", "Core i5 16GB", "5"])
        writer.writerow(["A2", "Принтер", "Xerox", "B230 A4", "2"])
        writer.writerow(["", "Всего", "", "", "7"])

    mapping = [
        ColumnMappingItem(index=0, role=ColumnRole.IGNORE),
        ColumnMappingItem(index=1, role=ColumnRole.NAME),
        ColumnMappingItem(index=2, role=ColumnRole.DESCRIPTION),
        ColumnMappingItem(index=3, role=ColumnRole.CHARACTERISTICS),
        ColumnMappingItem(index=4, role=ColumnRole.QUANTITY),
    ]

    items = parse_rows(csv_file, header_row=0, column_mapping=mapping)

    assert len(items) == 2
    # Проверка склейки слева направо (name -> description -> characteristics)
    assert items[0].row_index == 0
    assert items[0].raw_text == "Ноутбук; Lenovo IdeaPad; Core i5 16GB"
    assert items[0].raw_row == ["A1", "Ноутбук", "Lenovo IdeaPad", "Core i5 16GB", "5"]

    assert items[1].row_index == 1
    assert items[1].raw_text == "Принтер; Xerox; B230 A4"


def test_parse_rows_empty_name_with_description(tmp_path: Path):
    # Крайний случай 8 раздела: ячейка name пустая, но description есть -> позиция создаётся
    csv_file = tmp_path / "empty_name.csv"
    with open(csv_file, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f, delimiter=";")
        writer.writerow(["Наименование", "Тех. описание"])
        writer.writerow(["", "Картридж лазерный для принтера HP"])

    mapping = [
        ColumnMappingItem(index=0, role=ColumnRole.NAME),
        ColumnMappingItem(index=1, role=ColumnRole.DESCRIPTION),
    ]

    items = parse_rows(csv_file, header_row=0, column_mapping=mapping)
    assert len(items) == 1
    assert items[0].raw_text == "Картридж лазерный для принтера HP"


def test_parse_rows_limit_exceeded(tmp_path: Path):
    # Проверка лимита 1000 строк в задаче
    csv_file = tmp_path / "large_spec.csv"
    with open(csv_file, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f, delimiter=";")
        writer.writerow(["Наименование", "Кол-во"])
        for i in range(1005):
            writer.writerow([f"Позиция {i}", "1"])

    mapping = [
        ColumnMappingItem(index=0, role=ColumnRole.NAME),
        ColumnMappingItem(index=1, role=ColumnRole.QUANTITY),
    ]

    with pytest.raises(LimitExceededError):
        parse_rows(csv_file, header_row=0, column_mapping=mapping)


def test_parse_rows_row_index_with_skipped_rows_and_header_offset(tmp_path: Path):
    """
    Проверяет, что row_index соответствует исходному положению строки относительно заголовка,
    а пропущенные пустые строки и строки «Итого» не сбивают нумерацию последующих позиций.
    """
    csv_file = tmp_path / "spec_with_gaps.csv"
    with open(csv_file, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f, delimiter=";")
        writer.writerow(["Спецификация №1", ""])  # строка 0: шапка над таблицей
        writer.writerow(["Наименование", "Кол-во"])  # строка 1: заголовок таблицы (header_row = 1)
        writer.writerow(["Перчатки", "10"])  # строка 2 (индекс 0 после заголовка)
        writer.writerow(["", ""])  # строка 3 (индекс 1 после заголовка, пустая -> пропуск)
        writer.writerow(["Бумага А4", "5"])  # строка 4 (индекс 2 после заголовка)
        writer.writerow(["Итого", "15"])  # строка 5 (индекс 3 после заголовка, итого -> пропуск)
        writer.writerow(["Скрепки", "100"])  # строка 6 (индекс 4 после заголовка)

    mapping = [
        ColumnMappingItem(index=0, role=ColumnRole.NAME),
        ColumnMappingItem(index=1, role=ColumnRole.QUANTITY),
    ]

    items = parse_rows(csv_file, header_row=1, column_mapping=mapping)
    assert len(items) == 3

    assert items[0].row_index == 0
    assert items[0].raw_text == "Перчатки"

    assert items[1].row_index == 2
    assert items[1].raw_text == "Бумага А4"

    assert items[2].row_index == 4
    assert items[2].raw_text == "Скрепки"


def test_parse_rows_long_text_preserved(tmp_path: Path):
    """
    Проверяет крайний случай раздела 8:
    Длинный текст (> 2000 символов) полностью сохраняется в raw_text и raw_row.
    """
    csv_file = tmp_path / "long_text_spec.csv"
    long_desc = "Характеристика " + ("очень подробное описание товара " * 80)
    assert len(long_desc) > 2000

    with open(csv_file, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f, delimiter=";")
        writer.writerow(["Наименование", "Технические требования"])
        writer.writerow(["Серверный шкаф 42U", long_desc])

    mapping = [
        ColumnMappingItem(index=0, role=ColumnRole.NAME),
        ColumnMappingItem(index=1, role=ColumnRole.DESCRIPTION),
    ]

    items = parse_rows(csv_file, header_row=0, column_mapping=mapping)
    assert len(items) == 1
    assert items[0].raw_text.startswith("Серверный шкаф 42U; Характеристика")  # колонки склеиваются через «; »
    assert len(items[0].raw_text) > 2000
    assert items[0].raw_row[1] == long_desc.strip()


def test_parse_rows_file_matrix_limit_exceeded(tmp_path: Path, monkeypatch):
    """
    Проверяет крайний случай раздела 8:
    Файл больше 5000 строк вызывает LimitExceededError с подсказкой разбить файл.
    """
    csv_file = tmp_path / "exceed_matrix.csv"
    # Для быстроты теста понижаем лимит через monkeypatch
    monkeypatch.setattr("backend.files.parser.MAX_ROWS_IN_FILE", 10)

    with open(csv_file, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f, delimiter=";")
        writer.writerow(["Наименование", "Кол-во"])
        for i in range(15):
            writer.writerow([f"Товар {i}", "1"])

    mapping = [
        ColumnMappingItem(index=0, role=ColumnRole.NAME),
        ColumnMappingItem(index=1, role=ColumnRole.QUANTITY),
    ]

    with pytest.raises(LimitExceededError) as exc_info:
        parse_rows(csv_file, header_row=0, column_mapping=mapping)
    assert "превышает лимит 10" in exc_info.value.message

