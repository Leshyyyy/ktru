"""Пакет backend.files — разбор спецификаций и экспорт результатов."""

from backend.files.reader import read_file
from backend.files.parser import parse_rows
from backend.files.schemas import (
    ColumnInfo,
    ColumnMappingItem,
    ColumnRole,
    FileInfo,
    LayoutHint,
    ParsedRow,
    Sheet,
)
from backend.files.exceptions import (
    FileParsingError,
    FileTooLargeError,
    FileUnreadableError,
    LimitExceededError,
    MappingInvalidError,
    UnsupportedTypeError,
)

__all__ = [
    "read_file",
    "parse_rows",
    "FileInfo",
    "Sheet",
    "ColumnInfo",
    "ColumnRole",
    "ColumnMappingItem",
    "LayoutHint",
    "ParsedRow",
    "FileParsingError",
    "FileUnreadableError",
    "UnsupportedTypeError",
    "FileTooLargeError",
    "MappingInvalidError",
    "LimitExceededError",
]
