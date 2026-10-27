"""Тесты предпросмотра файлов спецификаций (read_file)."""

import csv
from pathlib import Path
import pytest
import openpyxl

from backend.files.exceptions import (
    FileTooLargeError,
    FileUnreadableError,
    UnsupportedTypeError,
)
from backend.files.reader import read_file
from backend.files.schemas import ColumnRole


def test_read_csv_utf8(tmp_path: Path):
    csv_file = tmp_path / "test_spec.csv"
    with open(csv_file, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f, delimiter=";")
        writer.writerow(["Наименование", "Кол-во", "Ед. изм."])
        writer.writerow(["Бумага офисная А4", "10", "пачка"])
        writer.writerow(["Ручка шариковая синяя", "50", "шт"])
        writer.writerow(["Итого", "", ""])

    file_info = read_file(csv_file)

    assert file_info.filename == "test_spec.csv"
    assert file_info.size_bytes > 0
    assert len(file_info.sheets) == 1

    sheet = file_info.sheets[0]
    assert sheet.row_count == 2  # Строка "Итого" исключена
    assert len(sheet.columns) == 3
    assert sheet.columns[0].suggested_role == ColumnRole.NAME
    assert sheet.columns[1].suggested_role == ColumnRole.QUANTITY
    assert sheet.columns[2].suggested_role == ColumnRole.UNIT
    assert len(sheet.preview) == 2
    assert sheet.preview[0][0] == "Бумага офисная А4"


def test_read_csv_utf8_bom_and_comma(tmp_path: Path):
    csv_file = tmp_path / "bom_comma.csv"
    with open(csv_file, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f, delimiter=",")
        writer.writerow(["Товар", "Характеристики", "Количество"])
        writer.writerow(["Картридж лазерный", "Ресурс 2500 стр.", "5"])

    file_info = read_file(csv_file)
    sheet = file_info.sheets[0]
    assert sheet.row_count == 1
    assert sheet.columns[0].header == "Товар"
    assert sheet.columns[0].suggested_role == ColumnRole.NAME


def test_read_csv_cp1251(tmp_path: Path):
    csv_file = tmp_path / "spec_1251.csv"
    with open(csv_file, "w", encoding="windows-1251", newline="") as f:
        f.write("Позиция;Количество;Единица\n")
        f.write("Монитор 27 дюймов;3;шт\n")

    file_info = read_file(csv_file)
    sheet = file_info.sheets[0]
    assert sheet.columns[0].header == "Позиция"
    assert sheet.columns[0].suggested_role == ColumnRole.NAME
    assert sheet.preview[0][0] == "Монитор 27 дюймов"


def test_read_xlsx_multi_sheet(tmp_path: Path):
    wb = openpyxl.Workbook()
    ws1 = wb.active
    ws1.title = "Спецификация"
    ws1.append(["№", "Наименование товара", "Кол-во"])
    ws1.append([1, "Ноутбук Lenovo 15IAU7", 10])
    ws1.append([2, "Мышь проводная", 10])

    ws2 = wb.create_sheet(title="Запасной лист")
    ws2.append(["Товар", "Примечание"])
    ws2.append(["Сетевой фильтр", "3 метра"])

    xlsx_path = tmp_path / "multi_sheet.xlsx"
    wb.save(xlsx_path)

    file_info = read_file(xlsx_path)
    assert len(file_info.sheets) == 2
    assert file_info.sheets[0].name == "Спецификация"
    assert file_info.sheets[0].row_count == 2
    assert file_info.sheets[1].name == "Запасной лист"
    assert file_info.sheets[1].row_count == 1


def test_read_unsupported_format(tmp_path: Path):
    txt_file = tmp_path / "file.txt"
    txt_file.write_text("Hello world", encoding="utf-8")
    with pytest.raises(UnsupportedTypeError):
        read_file(txt_file)


def test_read_empty_csv(tmp_path: Path):
    empty_csv = tmp_path / "empty.csv"
    empty_csv.write_text("", encoding="utf-8")
    with pytest.raises(FileUnreadableError):
        read_file(empty_csv)


def test_file_too_large(tmp_path: Path, monkeypatch):
    test_file = tmp_path / "fake_large.xlsx"
    test_file.write_bytes(b"A" * 200)
    # Понижаем MAX_FILE_SIZE для проверки срабатывания лимита
    monkeypatch.setattr("backend.files.reader.MAX_FILE_SIZE", 100)
    with pytest.raises(FileTooLargeError):
        read_file(test_file)
