"""Быстрый предпросмотр файлов спецификаций (read_file) для POST /files."""

import csv
import io
from pathlib import Path
from typing import Any, List, Optional, Tuple, Union
from uuid import UUID, uuid4

import charset_normalizer
from python_calamine import CalamineWorkbook
import pdfplumber

from backend.files.clean import clean_cell, is_empty_row, is_header_repeat, is_summary_row
from backend.files.exceptions import (
    FileParsingError,
    FileTooLargeError,
    FileUnreadableError,
    LimitExceededError,
    UnsupportedTypeError,
)
from backend.files.hash import compute_file_sha256
from backend.files.roles import suggest_roles
from backend.files.schemas import ColumnInfo, FileInfo, Sheet

MAX_FILE_SIZE = 50 * 1024 * 1024  # 50 МБ
# Больше строк parser.py не разберёт, поэтому read_file их и не читает: иначе файл на 50 МБ — десятки секунд и сотни
# мегабайт памяти ради предпросмотра в 10 строк. Запас нужен на пустые и служебные строки над шапкой; точную проверку
# делает parser.py при создании задачи.
MAX_ROWS_IN_FILE = 5000
_ROWS_SLACK = 50


def _too_many_rows(sheet_name: Optional[str] = None) -> LimitExceededError:
    where = f"На листе «{sheet_name}»" if sheet_name else "В файле"
    return LimitExceededError(f"{where} больше {MAX_ROWS_IN_FILE} строк. Разбейте файл на части.")


def _detect_csv_encoding_and_delimiter(file_path: Path) -> Tuple[str, str]:
    """Определяет кодировку и разделитель CSV-файла."""
    with open(file_path, "rb") as f:
        raw_bytes = f.read(65536)

    if not raw_bytes:
        raise FileUnreadableError("CSV-файл пуст.")

    # Проверка кодировки
    encoding = "utf-8"
    if raw_bytes.startswith(b"\xef\xbb\xbf"):
        encoding = "utf-8-sig"
    else:
        try:
            raw_bytes.decode("utf-8")
            encoding = "utf-8"
        except UnicodeDecodeError:
            detected = charset_normalizer.from_bytes(raw_bytes).best()
            encoding = detected.encoding if detected and detected.encoding else "windows-1251"

    # Определение разделителя
    text_sample = raw_bytes.decode(encoding, errors="replace")
    sample_lines = [line for line in text_sample.splitlines() if line.strip()][:10]
    sample_text = "\n".join(sample_lines)

    delimiter = ";"
    try:
        sniffer = csv.Sniffer()
        dialect = sniffer.sniff(sample_text, delimiters=";,	,")
        delimiter = dialect.delimiter
    except Exception:
        # Простая эвристика частоты разделителей
        counts = {
            ";": sample_text.count(";"),
            ",": sample_text.count(","),
            "\t": sample_text.count("\t"),
        }
        delimiter = max(counts, key=counts.get) if any(counts.values()) else ";"

    return encoding, delimiter


def _extract_sheet_from_matrix(
    name: str,
    raw_matrix: List[List[Any]],
    is_pdf: bool = False,
) -> Optional[Sheet]:
    """Формирует объект Sheet из двумерной матрицы строк."""
    if not raw_matrix:
        return None

    # Очистка ячеек
    matrix = [[clean_cell(cell) for cell in row] for row in raw_matrix]

    # Находим предполагаемую строку заголовка (первая строка с >= 2 непустыми ячейками)
    header_idx = 0
    for idx, row in enumerate(matrix):
        non_empty = [c for c in row if c]
        if len(non_empty) >= 2 and not is_summary_row(row):
            header_idx = idx
            break

    if header_idx >= len(matrix):
        return None

    raw_headers = matrix[header_idx]
    num_cols = len(raw_headers)
    if num_cols == 0:
        return None

    # Собираем данные после заголовка
    data_rows: List[List[str]] = []
    for row in matrix[header_idx + 1:]:
        # Дополняем строку пустыми ячейками до num_cols при необходимости
        if len(row) < num_cols:
            row = row + [""] * (num_cols - len(row))
        else:
            row = row[:num_cols]

        if is_empty_row(row):
            continue
        if is_summary_row(row):
            continue
        if is_header_repeat(row, raw_headers):
            continue

        data_rows.append(row)

    if not data_rows and not any(raw_headers):
        return None

    # Приводим пустые заголовки к стандартному виду: "Колонка 1", "Колонка 2"...
    headers = [
        raw_headers[i] if raw_headers[i] else f"Колонка {i + 1}"
        for i in range(num_cols)
    ]

    # Сэмплы для каждой колонки (первые до 5 значений)
    samples_by_col: List[List[str]] = []
    for col_idx in range(num_cols):
        col_samples = [
            row[col_idx]
            for row in data_rows[:10]
            if col_idx < len(row) and row[col_idx].strip()
        ]
        samples_by_col.append(col_samples[:5])

    # Определяем роли колонок
    suggested = suggest_roles(headers, samples_by_col)

    columns = [
        ColumnInfo(
            index=i,
            header=headers[i],
            sample=samples_by_col[i],
            suggested_role=suggested[i],
        )
        for i in range(num_cols)
    ]

    preview_rows = data_rows[:10]
    row_count = None if is_pdf else len(data_rows)

    return Sheet(
        name=name,
        row_count=row_count,
        header_row=header_idx,
        columns=columns,
        preview=preview_rows,
    )


def read_file(
    path: Union[str, Path],
    original_filename: Optional[str] = None,
    file_id: Optional[UUID] = None,
) -> FileInfo:
    """
    Быстро читает файл спецификации и возвращает метаданные, структуру листов и колонок.
    Отвечает < 2 секунд. Для PDF разбирает только первые 3 страницы, row_count=None.
    """
    file_path = Path(path)
    if not file_path.exists():
        raise FileUnreadableError(f"Файл не найден: {file_path}")

    size_bytes = file_path.stat().st_size
    if size_bytes > MAX_FILE_SIZE:
        raise FileTooLargeError()

    filename = original_filename or file_path.name
    ext = Path(filename).suffix.lower()

    sheets: List[Sheet] = []

    if ext in (".xlsx", ".xls"):
        try:
            wb = CalamineWorkbook.from_path(str(file_path))
            for sheet_name in wb.sheet_names:
                sheet = wb.get_sheet_by_name(sheet_name)
                # Размер листа известен до to_python(): лишнее не читаем.
                if sheet.total_height > MAX_ROWS_IN_FILE + _ROWS_SLACK:
                    raise _too_many_rows(sheet_name)
                sheet_data = sheet.to_python()
                parsed_sheet = _extract_sheet_from_matrix(sheet_name, sheet_data, is_pdf=False)
                if parsed_sheet:
                    sheets.append(parsed_sheet)
        except FileParsingError:
            raise  # LimitExceededError не должен превратиться в «ошибку чтения файла»
        except Exception as e:
            raise FileUnreadableError(f"Ошибка чтения Excel-файла: {e}") from e

    elif ext == ".csv":
        try:
            encoding, delimiter = _detect_csv_encoding_and_delimiter(file_path)
            rows: List[List[str]] = []
            with open(file_path, "r", encoding=encoding, errors="replace", newline="") as f:
                reader = csv.reader(f, delimiter=delimiter)
                for r in reader:
                    rows.append(r)
                    if len(rows) > MAX_ROWS_IN_FILE + _ROWS_SLACK:
                        raise _too_many_rows()

            parsed_sheet = _extract_sheet_from_matrix("Лист1", rows, is_pdf=False)
            if parsed_sheet:
                sheets.append(parsed_sheet)
        except FileParsingError:
            raise
        except Exception as e:
            raise FileUnreadableError(f"Ошибка чтения CSV-файла: {e}") from e

    elif ext == ".pdf":
        try:
            table_rows: List[List[str]] = []
            with pdfplumber.open(file_path) as pdf:
                # Читаем максимум первые 3 страницы для быстрого ответа
                pages_to_read = pdf.pages[:3]
                for page in pages_to_read:
                    tables = page.extract_tables()
                    for tbl in tables:
                        if tbl:
                            table_rows.extend(tbl)

            if not table_rows:
                # Если таблицы не выделены явно, пробуем разбить построчно
                with pdfplumber.open(file_path) as pdf:
                    for page in pdf.pages[:3]:
                        text = page.extract_text()
                        if text:
                            for line in text.splitlines():
                                parts = [p.strip() for p in line.split("  ") if p.strip()]
                                if len(parts) >= 2:
                                    table_rows.append(parts)

            parsed_sheet = _extract_sheet_from_matrix("Таблицы", table_rows, is_pdf=True)
            if parsed_sheet:
                sheets.append(parsed_sheet)
        except Exception as e:
            raise FileUnreadableError(f"Ошибка чтения PDF-файла: {e}") from e

    else:
        raise UnsupportedTypeError()

    if not sheets:
        raise FileUnreadableError("Файл не содержит читаемых таблиц или строк данных.")

    return FileInfo(
        file_id=file_id or uuid4(),
        filename=filename,
        size_bytes=size_bytes,
        duplicate_of_job_id=None,
        layout_hint=None,
        sheets=sheets,
    )
