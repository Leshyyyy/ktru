"""XLSX «для заявки»: строка на характеристику итогового кода.

Временная сборка на стороне бэкенда: пока `backend/files` не умеет `format=application`,
маршрут выгрузки использует этот модуль. Когда генератор появится у владельца файлового
модуля, вызов заменяется на его функцию без изменения структуры данных.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import openpyxl
from openpyxl.styles import Font, PatternFill

from backend.files.exceptions import UnreviewedItemsError
from backend.files.export import UNREVIEWED_STATUSES, format_content_disposition
from backend.files.schemas import ExportResult

MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

COLUMNS = [
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

SOURCE_USER = "пользователь"
SOURCE_TEXT = "текст позиции"
SOURCE_MISSING = "не заполнено"


def export_application(
    items: list[dict[str, Any]], original_filename: str = "specification.xlsx", allow_unreviewed: bool = True
) -> ExportResult:
    """Собирает xlsx, где каждая позиция занимает столько строк, сколько у неё характеристик."""
    unreviewed_count = sum(1 for item in items if item.get("status") in UNREVIEWED_STATUSES)
    if not allow_unreviewed and unreviewed_count:
        raise UnreviewedItemsError(
            f"Осталось {unreviewed_count} непроверенных позиций.",
            details={"unreviewed": unreviewed_count},
        )

    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "Заявка"

    header_font = Font(bold=True, color="000000")
    header_fill = PatternFill(start_color="F0F2F5", end_color="F0F2F5", fill_type="solid")
    for column, title in enumerate(COLUMNS, start=1):
        cell = sheet.cell(row=1, column=column)
        _set_text(cell, title)
        cell.font = header_font
        cell.fill = header_fill

    row = 2
    for item in items:
        for values in _item_rows(item):
            for column, value in enumerate(values, start=1):
                _set_text(sheet.cell(row=row, column=column), value)
            row += 1

    for column in sheet.columns:
        width = max(len(str(cell.value or "")) for cell in column)
        sheet.column_dimensions[column[0].column_letter].width = min(max(width + 3, 12), 60)

    buffer = io.BytesIO()
    workbook.save(buffer)
    filename = f"{Path(original_filename).stem or 'export'}_application.xlsx"
    return ExportResult(
        content=buffer.getvalue(),
        media_type=MEDIA_TYPE,
        filename=filename,
        unreviewed_count=unreviewed_count,
        content_disposition=format_content_disposition(filename),
    )


def _item_rows(item: dict[str, Any]) -> list[list[str]]:
    base = [
        str(item.get("row_index", 0) + 1),
        item.get("raw_name") or "",
        item.get("ktru_code") or "",
        item.get("ktru_name") or "",
    ]
    unit = item.get("unit") or ""
    characteristics = item.get("characteristics") or []
    if not item.get("ktru_code") or not characteristics:
        # Позиция не пропадает из заявки: код и наименование (если есть), характеристики пустые.
        return [[*base, "", "", unit, "", ""]]

    rows = []
    for characteristic in characteristics:
        value = characteristic.get("value")
        if value is None:
            value = characteristic.get("found")
        status = characteristic.get("status")
        required = bool(characteristic.get("required"))
        if status == "filled":
            source = SOURCE_USER
        elif status == "missing" and required:
            source, value = SOURCE_MISSING, None
        elif value is not None:
            source = SOURCE_TEXT
        else:
            source = ""
        rows.append(
            [
                *base,
                characteristic.get("name") or "",
                value or "",
                unit,
                "да" if required else "нет",
                source,
            ]
        )
    return rows


def _set_text(cell: Any, value: str) -> None:
    """Все ячейки — строки: openpyxl не должен превратить «=…» в формулу."""
    cell.value = "" if value is None else str(value)
    cell.data_type = "s"
