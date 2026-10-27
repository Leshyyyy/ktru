"""Полный разбор строк спецификации (parse_rows) для Celery-задачи parse_file."""

import csv
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from python_calamine import CalamineWorkbook
import pdfplumber

from backend.files.clean import clean_cell, is_empty_row, is_header_repeat, is_summary_row
from backend.files.exceptions import (
    FileParsingError,
    FileUnreadableError,
    LimitExceededError,
    MappingInvalidError,
    UnsupportedTypeError,
)
from backend.files.reader import _detect_csv_encoding_and_delimiter
from backend.files.schemas import ColumnMappingItem, ColumnRole, ParsedRow

MAX_ITEMS_PER_JOB = 1000
MAX_ROWS_IN_FILE = 5000


def validate_column_mapping(mapping: Union[List[ColumnMappingItem], List[Dict[str, Any]]]) -> Dict[int, ColumnRole]:
    """
    Проверяет бизнес-правила сопоставления колонок:
    - Ровно одна колонка с ролью 'name'.
    - Не более одной колонки 'quantity'.
    - Не более одной колонки 'unit'.
    Возвращает словарь {index: ColumnRole}.
    """
    role_by_index: Dict[int, ColumnRole] = {}

    for item in mapping:
        if isinstance(item, ColumnMappingItem):
            idx = item.index
            role = item.role
        elif isinstance(item, dict):
            idx = int(item["index"])
            role = ColumnRole(item["role"])
        else:
            raise MappingInvalidError(f"Недопустимый формат элемента маппинга: {item}")

        role_by_index[idx] = role

    # Считаем количество каждой роли
    name_count = sum(1 for r in role_by_index.values() if r == ColumnRole.NAME)
    quantity_count = sum(1 for r in role_by_index.values() if r == ColumnRole.QUANTITY)
    unit_count = sum(1 for r in role_by_index.values() if r == ColumnRole.UNIT)

    if name_count == 0:
        raise MappingInvalidError(
            "Не выбрана колонка с наименованием товара. Требуется ровно одна колонка с ролью 'name'.",
            details={"error_code": "mapping_invalid", "reason": "name_required"},
        )
    if name_count > 1:
        raise MappingInvalidError(
            f"Выбрано {name_count} колонок с ролью 'name'. Допускается ровно одна колонка с наименованием.",
            details={"error_code": "mapping_invalid", "reason": "multiple_name_columns"},
        )
    if quantity_count > 1:
        raise MappingInvalidError(
            f"Выбрано {quantity_count} колонок с ролью 'quantity'. Допускается не более одной колонки количества.",
            details={"error_code": "mapping_invalid", "reason": "multiple_quantity_columns"},
        )
    if unit_count > 1:
        raise MappingInvalidError(
            f"Выбрано {unit_count} колонок с ролью 'unit'. Допускается не более одной колонки единицы измерения.",
            details={"error_code": "mapping_invalid", "reason": "multiple_unit_columns"},
        )

    return role_by_index


def _load_raw_matrix(
    file_path: Path,
    sheet_name: Optional[str] = None,
) -> List[List[Any]]:
    """Загружает полную матрицу строк для указанного файла и листа."""
    ext = file_path.suffix.lower()

    if ext in (".xlsx", ".xls"):
        try:
            wb = CalamineWorkbook.from_path(str(file_path))
            target_sheet = sheet_name or wb.sheet_names[0]
            if target_sheet not in wb.sheet_names:
                raise FileUnreadableError(f"Лист '{target_sheet}' не найден в файле.")
            return wb.get_sheet_by_name(target_sheet).to_python()
        except Exception as e:
            raise FileUnreadableError(f"Ошибка загрузки Excel-файла: {e}") from e

    elif ext == ".csv":
        try:
            encoding, delimiter = _detect_csv_encoding_and_delimiter(file_path)
            rows: List[List[str]] = []
            with open(file_path, "r", encoding=encoding, errors="replace", newline="") as f:
                reader = csv.reader(f, delimiter=delimiter)
                for r in reader:
                    rows.append(r)
            return rows
        except Exception as e:
            raise FileUnreadableError(f"Ошибка загрузки CSV-файла: {e}") from e

    elif ext == ".pdf":
        try:
            rows = []
            with pdfplumber.open(file_path) as pdf:
                for page in pdf.pages:
                    tables = page.extract_tables()
                    for tbl in tables:
                        if tbl:
                            rows.extend(tbl)
            if not rows:
                with pdfplumber.open(file_path) as pdf:
                    for page in pdf.pages:
                        text = page.extract_text()
                        if text:
                            for line in text.splitlines():
                                parts = [p.strip() for p in line.split("  ") if p.strip()]
                                if len(parts) >= 2:
                                    rows.append(parts)
            return rows
        except Exception as e:
            raise FileUnreadableError(f"Ошибка загрузки PDF-файла: {e}") from e

    else:
        raise UnsupportedTypeError()


def parse_rows(
    path: Union[str, Path],
    sheet_name: Optional[str] = None,
    header_row: int = 0,
    column_mapping: Union[List[ColumnMappingItem], List[Dict[str, Any]]] = None,
) -> List[ParsedRow]:
    """
    Разбирает все строки файла спецификации согласно выбранному сопоставлению колонок.
    Вызывается из фоновой Celery-задачи parse_file.
    Формирует raw_text и raw_row для сохранения в таблицу items.
    """
    if column_mapping is None:
        raise MappingInvalidError("Сопоставление колонок (column_mapping) не задано.")

    file_path = Path(path)
    if not file_path.exists():
        raise FileUnreadableError(f"Файл не найден: {file_path}")

    role_by_index = validate_column_mapping(column_mapping)

    # Индексы колонок, участвующих в склейке raw_text: name, description, characteristics
    # Сортируем слева направо по возрастанию индекса колонки в исходном файле
    text_col_indices = sorted(
        idx
        for idx, role in role_by_index.items()
        if role in (ColumnRole.NAME, ColumnRole.DESCRIPTION, ColumnRole.CHARACTERISTICS)
    )

    raw_matrix = _load_raw_matrix(file_path, sheet_name=sheet_name)
    if not raw_matrix or header_row >= len(raw_matrix):
        raise FileUnreadableError("Файл не содержит заголовка или строк данных.")

    if len(raw_matrix) > MAX_ROWS_IN_FILE:
        raise LimitExceededError(
            f"Файл содержит {len(raw_matrix)} строк, что превышает лимит {MAX_ROWS_IN_FILE}. Разбейте файл на части."
        )

    clean_headers = [clean_cell(c) for c in raw_matrix[header_row]]
    num_cols = len(clean_headers)

    parsed_rows: List[ParsedRow] = []
    total_data_rows_seen = 0

    for current_row_idx, raw_cells in enumerate(raw_matrix[header_row + 1:]):
        # Приводим ячейки к очищенным строкам
        cleaned_cells = [clean_cell(c) for c in raw_cells]
        if len(cleaned_cells) < num_cols:
            cleaned_cells = cleaned_cells + [""] * (num_cols - len(cleaned_cells))

        if is_empty_row(cleaned_cells):
            continue
        if is_summary_row(cleaned_cells):
            continue
        if is_header_repeat(cleaned_cells, clean_headers):
            continue

        total_data_rows_seen += 1
        if total_data_rows_seen > MAX_ITEMS_PER_JOB:
            raise LimitExceededError(
                f"Количество позиций спецификации превысило лимит {MAX_ITEMS_PER_JOB}. Разбейте файл на части."
            )

        # Сборка raw_text: склейка непустых значений колонок name, description, characteristics слева направо
        text_parts = []
        for c_idx in text_col_indices:
            if c_idx < len(cleaned_cells):
                val = cleaned_cells[c_idx]
                if val:
                    text_parts.append(val)

        raw_text = " ".join(text_parts).strip()

        # Если даже после объединения текст пустой — пропускаем такую позицию
        if not raw_text:
            continue

        parsed_rows.append(
            ParsedRow(
                row_index=len(parsed_rows),
                raw_text=raw_text,
                raw_row=cleaned_cells,
            )
        )

    if not parsed_rows:
        raise FileUnreadableError("В файле не найдено валидных строк данных для обработки.")

    return parsed_rows
