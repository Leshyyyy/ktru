"""Схемы запросов и ответов API — повторяют components.schemas из contracts/openapi.yaml (контракт важнее).

FileInfo и вложенные в неё схемы описаны в backend/files/schemas.py: их возвращает read_file.
ExtractedAttributes и CharacteristicMatch — те же модели, что в contracts/ml_contracts.py.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from contracts.ml_contracts import CharacteristicMatch, ExtractedAttributes

ColumnRole = Literal["name", "description", "characteristics", "quantity", "unit", "ignore"]
JobStatus = Literal["queued", "parsing", "processing", "done", "done_with_errors", "failed", "cancelled"]
JobErrorCode = Literal["file_unreadable", "mapping_invalid", "limit_exceeded", "internal_error"]
ItemStatus = Literal["pending", "processed", "needs_review", "confirmed", "corrected", "failed"]
ItemFlag = Literal["required_missing", "archived_code", "low_extraction_confidence"]
Band = Literal["green", "yellow", "red"]


# --- Задачи ---------------------------------------------------------------------------------------------------


class ColumnMappingEntry(BaseModel):
    index: int = Field(ge=0)
    role: ColumnRole


class JobOptions(BaseModel):
    use_llm: bool = True
    use_feedback: bool = True


class JobCreate(BaseModel):
    file_id: uuid.UUID
    sheet: str | None = None
    header_row: int = Field(0, ge=0)
    column_mapping: list[ColumnMappingEntry] = Field(min_length=1)
    options: JobOptions = Field(default_factory=JobOptions)


class JobSummary(BaseModel):
    green: int
    yellow: int
    red: int
    confirmed: int
    corrected: int


class Job(BaseModel):
    job_id: uuid.UUID
    status: JobStatus
    file_id: uuid.UUID
    filename: str
    total_items: int
    processed_items: int
    failed_items: int
    progress: float
    eta_seconds: int | None
    effective_mode: Literal["normal", "degraded"]
    catalog_version_id: uuid.UUID
    summary: JobSummary
    error: str | None
    error_code: JobErrorCode | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class JobList(BaseModel):
    items: list[Job]
    total: int
    page: int
    page_size: int


# --- Позиции --------------------------------------------------------------------------------------------------


class Prediction(BaseModel):
    rank: int
    ktru_code: str
    okpd2_code: str
    name: str
    confidence: float
    explanation: str | None
    why_not_chosen: str | None
    characteristic_matches: list[CharacteristicMatch]


class FinalAnswer(BaseModel):
    ktru_code: str | None
    name: str | None
    source: Literal["prediction", "user"]
    confidence: float | None


class Item(BaseModel):
    item_id: uuid.UUID
    job_id: uuid.UUID
    row_index: int
    raw_text: str
    status: ItemStatus
    band: Band | None
    path: Literal["fast", "rerank"] | None
    flags: list[ItemFlag]
    group_id: uuid.UUID | None
    group_size: int
    extracted: ExtractedAttributes | None
    predictions: list[Prediction]
    final: FinalAnswer | None
    error: str | None


class ItemList(BaseModel):
    items: list[Item]
    total: int
    page: int
    page_size: int


# --- Система --------------------------------------------------------------------------------------------------


class ComponentHealth(BaseModel):
    ok: bool
    detail: str | None = None


class HealthComponents(BaseModel):
    database: ComponentHealth
    redis: ComponentHealth
    models: ComponentHealth
    catalog: ComponentHealth


class Health(BaseModel):
    ready: bool
    components: HealthComponents


class Limits(BaseModel):
    max_file_mb: int
    max_rows: int
    max_items_per_job: int
    classify_max_items: int


class Thresholds(BaseModel):
    green: float
    yellow: float


class Mode(BaseModel):
    extractor: Literal["rules", "llm", "hybrid"]
    reranker: Literal["none", "cross_encoder", "llm"]
    device: Literal["cpu", "cuda"]
    llm_enabled: bool


class PublicConfig(BaseModel):
    limits: Limits
    thresholds: Thresholds
    mode: Mode
