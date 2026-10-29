"""Пакет backend.files — разбор спецификаций и экспорт результатов."""

from backend.files.hash import compute_file_sha256, compute_headers_hash
from backend.files.roles import suggest_roles
from backend.files.reader import read_file
from backend.files.parser import parse_rows
from backend.files.export import (
    escape_formula_for_csv,
    export_csv,
    export_job_results,
    export_json,
    export_xlsx,
    format_content_disposition,
)
from backend.files.schemas import (
    ColumnInfo,
    ColumnMappingItem,
    ColumnRole,
    ExportFormat,
    ExportItem,
    ExportResult,
    FileInfo,
    LayoutHint,
    ParsedRow,
    Sheet,
)
from backend.files.exceptions import (
    FileParsingError,
    FileTooLargeError,
    FileUnreadableError,
    InvalidStateError,
    LimitExceededError,
    MappingInvalidError,
    UnreviewedItemsError,
    UnsupportedTypeError,
)

__all__ = [
    "read_file",
    "parse_rows",
    "suggest_roles",
    "compute_file_sha256",
    "compute_headers_hash",
    "export_job_results",
    "export_xlsx",
    "export_csv",
    "export_json",
    "escape_formula_for_csv",
    "format_content_disposition",
    "FileInfo",
    "Sheet",
    "ColumnInfo",
    "ColumnRole",
    "ColumnMappingItem",
    "LayoutHint",
    "ParsedRow",
    "ExportFormat",
    "ExportItem",
    "ExportResult",
    "FileParsingError",
    "FileUnreadableError",
    "UnsupportedTypeError",
    "FileTooLargeError",
    "MappingInvalidError",
    "LimitExceededError",
    "UnreviewedItemsError",
    "InvalidStateError",
]

