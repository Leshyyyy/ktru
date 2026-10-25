"""Схемы ответов API — повторяют components.schemas из contracts/openapi.yaml (контракт важнее)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


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
