"""Исключения модуля разбора файлов."""

from typing import Any, Optional


class FileParsingError(Exception):
    """Базовое исключение разбора файлов."""

    def __init__(self, message: str, code: str = "file_parsing_error", details: Optional[Any] = None) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.details = details or {}


class FileUnreadableError(FileParsingError):
    """Файл повреждён, пуст или в нём отсутствуют читаемые строки данных (422)."""

    def __init__(self, message: str = "Файл пуст или не содержит читаемых строк с данными.", details: Optional[Any] = None) -> None:
        super().__init__(message, code="file_unreadable", details=details)


class UnsupportedTypeError(FileParsingError):
    """Формат файла не поддерживается (415)."""

    def __init__(self, message: str = "Поддерживаются файлы xlsx, csv и pdf.", details: Optional[Any] = None) -> None:
        super().__init__(message, code="unsupported_file_type", details=details)


class FileTooLargeError(FileParsingError):
    """Размер файла превышает лимит 50 МБ (413)."""

    def __init__(self, message: str = "Размер файла превышает допустимый лимит 50 МБ.", details: Optional[Any] = None) -> None:
        super().__init__(message, code="file_too_large", details=details)


class MappingInvalidError(FileParsingError):
    """Некорректное сопоставление колонок (422)."""

    def __init__(self, message: str, details: Optional[Any] = None) -> None:
        super().__init__(message, code="mapping_invalid", details=details)


class LimitExceededError(FileParsingError):
    """Превышен лимит строк в файле или в задаче (422)."""

    def __init__(self, message: str = "Превышен лимит строк (максимум 1000 строк в задаче или 5000 в файле). Разбейте файл на части.", details: Optional[Any] = None) -> None:
        super().__init__(message, code="limit_exceeded", details=details)
