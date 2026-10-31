"""Pydantic-схемы модуля разбора файлов в соответствии с contracts/openapi.yaml."""

from enum import Enum
from typing import List, Optional
from uuid import UUID, uuid4
from pydantic import BaseModel, ConfigDict, Field


class ColumnRole(str, Enum):
    """Роли колонок спецификации."""

    NAME = "name"
    DESCRIPTION = "description"
    CHARACTERISTICS = "characteristics"
    QUANTITY = "quantity"
    UNIT = "unit"
    IGNORE = "ignore"


class ColumnInfo(BaseModel):
    """Информация об обнаруженной колонке листа."""

    model_config = ConfigDict(populate_by_name=True)

    index: int = Field(ge=0, description="Индекс колонки (с 0)")
    header: str = Field(description="Заголовок колонки")
    sample: List[str] = Field(default_factory=list, description="Примеры значений из первых строк")
    suggested_role: ColumnRole = Field(description="Предложенная роль колонки")


class Sheet(BaseModel):
    """Информация о листе спецификации."""

    model_config = ConfigDict(populate_by_name=True)

    name: str = Field(description="Название листа")
    row_count: Optional[int] = Field(
        default=None,
        description="Число строк данных без заголовка. Для PDF всегда null",
    )
    header_row: int = Field(default=0, ge=0, description="Предполагаемая строка заголовка (с 0)")
    columns: List[ColumnInfo] = Field(default_factory=list, description="Колонки листа")
    preview: List[List[str]] = Field(
        default_factory=list,
        description="Первые до 10 строк данных",
    )


class ColumnMappingItem(BaseModel):
    """Элемент сопоставления колонки."""

    index: int = Field(ge=0, description="Индекс колонки (с 0)")
    role: ColumnRole = Field(description="Назначенная роль")


class LayoutHint(BaseModel):
    """Ранее подтверждённое пользователем сопоставление колонок."""

    header_row: int = Field(default=0, ge=0)
    column_mapping: List[ColumnMappingItem]


class FileInfo(BaseModel):
    """Ответ POST /files по схеме FileInfo из contracts/openapi.yaml."""

    file_id: UUID = Field(default_factory=uuid4, description="Идентификатор загруженного файла")
    filename: str = Field(description="Имя файла спецификации")
    size_bytes: int = Field(ge=0, description="Размер файла в байтах")
    duplicate_of_job_id: Optional[UUID] = Field(
        default=None,
        description="ID задачи, в которой уже обрабатывался файл с тем же sha256",
    )
    layout_hint: Optional[LayoutHint] = Field(
        default=None,
        description="Подсказка маппинга по совпадению заголовков",
    )
    sheets: List[Sheet] = Field(default_factory=list, description="Листы файла")


class ParsedRow(BaseModel):
    """Разобранная строка спецификации для Celery-задачи parse_file."""

    row_index: int = Field(ge=0, description="Порядковый номер строки в файле (с 0, без заголовка)")
    raw_text: str = Field(description="Склейка name + description + characteristics через «; »")
    raw_row: List[str] = Field(description="Исходные строковые значения всех колонок строки")


class ExportFormat(str, Enum):
    """Поддерживаемые форматы выгрузки результата."""

    SOURCE = "source"
    XLSX = "xlsx"
    CSV = "csv"
    JSON = "json"


class ExportItem(BaseModel):
    """Данные позиции для экспорта (соответствуют v_item_final + items)."""

    model_config = ConfigDict(populate_by_name=True)

    item_id: Optional[UUID] = None
    row_index: int = Field(default=0, ge=0)
    raw_row: List[str] = Field(default_factory=list, description="Исходные значения строки")
    ktru_code: Optional[str] = Field(default=None, description="Итоговый код КТРУ")
    ktru_name: Optional[str] = Field(default=None, description="Наименование позиции КТРУ")
    confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0, description="Уверенность от 0 до 1")
    status: str = Field(default="processed", description="Статус: pending, processed, needs_review, confirmed, corrected, failed")
    source: Optional[str] = Field(default=None, description="Источник: user или prediction")
    flags: List[str] = Field(default_factory=list, description="Предупреждения")
    error: Optional[str] = Field(default=None, description="Текст ошибки при failed")


class ExportResult(BaseModel):
    """Результат генерации файла экспорта."""

    content: bytes = Field(description="Байтовое содержимое файла")
    media_type: str = Field(description="MIME-тип для заголовка Content-Type")
    filename: str = Field(description="Имя файла для скачивания")
    unreviewed_count: int = Field(ge=0, description="Число непроверенных позиций (processed и needs_review)")
    content_disposition: str = Field(description="Заголовок Content-Disposition по RFC 5987 / RFC 6266")
