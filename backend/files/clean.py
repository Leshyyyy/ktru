"""Очистка и нормализация текстовых данных и строк спецификаций."""

import re
from typing import Any, List, Optional

_SUMMARY_PREFIXES = (
    "итого",
    "всего",
    "в том числе",
    "общая сумма",
    "всего по разделу",
    "всего к оплате",
    "итоговая сумма",
    "сумма ндс",
)

_QUOTE_RE = re.compile(r"[«»“”„‟]")
_WHITESPACE_RE = re.compile(r"[\s\u00a0]+")


def clean_cell(val: Any) -> str:
    """
    Приводит значение ячейки к строке, нормализует пробелы и кавычки.
    Регистр символов НЕ меняется.
    """
    if val is None:
        return ""
    if isinstance(val, float):
        if val.is_integer():
            text = str(int(val))
        else:
            text = f"{val:.4f}".rstrip("0").rstrip(".")
    else:
        text = str(val)

    # Замена кавычек на стандартные "
    text = _QUOTE_RE.sub('"', text)
    # Нормализация неразрывных и повторяющихся пробелов
    text = _WHITESPACE_RE.sub(" ", text).strip()
    return text


def is_empty_row(cells: List[str]) -> bool:
    """Проверяет, является ли строка полностью пустой."""
    return not any(c.strip() for c in cells)


def is_summary_row(cells: List[str]) -> bool:
    """
    Проверяет, является ли строка итоговой (Итого, Всего и т.д.).
    Итоговые строки должны исключаться из разбора позиций спецификации.
    """
    for cell in cells:
        c_lower = cell.strip().lower()
        if not c_lower:
            continue
        for prefix in _SUMMARY_PREFIXES:
            if c_lower == prefix or c_lower.startswith(prefix + " ") or c_lower.startswith(prefix + ":"):
                return True
    return False


def is_header_repeat(cells: List[str], header_cells: List[str]) -> bool:
    """
    Проверяет, является ли строка повтором заголовка таблицы (часто на новых страницах PDF).
    """
    if not cells or not header_cells:
        return False
    matched = 0
    checked = 0
    for c, h in zip(cells, header_cells):
        c_clean = c.strip().lower()
        h_clean = h.strip().lower()
        if h_clean:
            checked += 1
            if c_clean == h_clean:
                matched += 1
    return checked > 0 and (matched / checked) >= 0.6
