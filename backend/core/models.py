"""SQLAlchemy-модели таблиц из contracts/schema.sql.

Схему создают миграции (backend/migrations), а не эти классы: модели только описывают таблицы для запросов.
Ограничения (CHECK, UNIQUE, индексы) живут в базе. Тест test_models_match_database следит, чтобы колонки
моделей совпадали с колонками после миграций.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    REAL,
    BigInteger,
    Boolean,
    Column,
    Computed,
    DateTime,
    ForeignKey,
    Integer,
    MetaData,
    SmallInteger,
    Table,
    Text,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TSVECTOR
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

EMBEDDING_DIM = 1024  # = vector(1024) в schema.sql и EMBEDDING_DIM у ML

_UUID_DEFAULT = text("gen_random_uuid()")
_NOW = text("now()")


class Base(DeclarativeBase):
    pass


def _id() -> Mapped[uuid.UUID]:
    return mapped_column(Uuid, primary_key=True, server_default=_UUID_DEFAULT)


# --- Каталог (владелец — ML) ----------------------------------------------------------------------


class CatalogVersion(Base):
    __tablename__ = "catalog_versions"

    id: Mapped[uuid.UUID] = _id()
    source: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default=text("'importing'"))
    items_count: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    note: Mapped[str | None] = mapped_column(Text)
    loaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=_NOW)


class CatalogItem(Base):
    __tablename__ = "catalog_items"

    id: Mapped[uuid.UUID] = _id()
    version_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("catalog_versions.id", ondelete="CASCADE"))
    ktru_code: Mapped[str] = mapped_column(Text)
    okpd2_code: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    unit: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default=text("'active'"))
    replaced_by: Mapped[str | None] = mapped_column(Text)
    characteristics: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    search_text: Mapped[str] = mapped_column(Text)
    embedding: Mapped[Any | None] = mapped_column(Vector(EMBEDDING_DIM), deferred=True)
    tsv: Mapped[Any | None] = mapped_column(
        TSVECTOR, Computed("to_tsvector('russian', search_text)", persisted=True), deferred=True
    )


# --- Обработка --------------------------------------------------------------------------------------


class SourceFile(Base):
    __tablename__ = "source_files"

    id: Mapped[uuid.UUID] = _id()
    filename: Mapped[str] = mapped_column(Text)
    mime_type: Mapped[str] = mapped_column(Text)
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[str] = mapped_column(Text)
    storage_path: Mapped[str] = mapped_column(Text)
    parsed_meta: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=_NOW)


class FileLayout(Base):
    __tablename__ = "file_layouts"

    header_hash: Mapped[str] = mapped_column(Text, primary_key=True)
    header_row: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    column_mapping: Mapped[dict[str, Any]] = mapped_column(JSONB)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=_NOW)


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[uuid.UUID] = _id()
    file_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("source_files.id"))
    catalog_version_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("catalog_versions.id"))
    status: Mapped[str] = mapped_column(Text, server_default=text("'queued'"))
    column_mapping: Mapped[dict[str, Any]] = mapped_column(JSONB)
    options: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    effective_mode: Mapped[str] = mapped_column(Text, server_default=text("'normal'"))
    total_items: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    processed_items: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    failed_items: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    cancel_requested: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    error: Mapped[str | None] = mapped_column(Text)
    error_code: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=_NOW)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Обновляет триггер jobs_set_updated_at; по нему watchdog находит зависшие задачи.
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=_NOW)


class Item(Base):
    __tablename__ = "items"

    id: Mapped[uuid.UUID] = _id()
    job_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("jobs.id", ondelete="CASCADE"))
    row_index: Mapped[int] = mapped_column(Integer)
    raw_text: Mapped[str] = mapped_column(Text)
    raw_row: Mapped[list[Any]] = mapped_column(JSONB)  # значения всех колонок строки по порядку (ParsedRow.raw_row)
    extracted: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(Text, server_default=text("'pending'"))
    path: Mapped[str | None] = mapped_column(Text)
    norm_key: Mapped[str | None] = mapped_column(Text)
    group_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    flags: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default=text("'{}'"))
    error: Mapped[str | None] = mapped_column(Text)
    timings_ms: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=_NOW)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Prediction(Base):
    __tablename__ = "predictions"

    id: Mapped[uuid.UUID] = _id()
    item_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("items.id", ondelete="CASCADE"))
    rank: Mapped[int] = mapped_column(SmallInteger)
    ktru_code: Mapped[str] = mapped_column(Text)
    confidence: Mapped[float] = mapped_column(REAL)
    features: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    characteristic_matches: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    explanation: Mapped[str | None] = mapped_column(Text)
    why_not_chosen: Mapped[str | None] = mapped_column(Text)


# --- Обратная связь -----------------------------------------------------------------------------------


class Decision(Base):
    __tablename__ = "decisions"

    id: Mapped[uuid.UUID] = _id()
    item_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("items.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(Text)
    via: Mapped[str] = mapped_column(Text, server_default=text("'manual'"))
    ktru_code: Mapped[str] = mapped_column(Text)
    old_code: Mapped[str | None] = mapped_column(Text)
    reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("clock_timestamp()"))


class ConfirmedExample(Base):
    __tablename__ = "confirmed_examples"

    id: Mapped[uuid.UUID] = _id()
    text: Mapped[str] = mapped_column(Text)
    norm_text: Mapped[str] = mapped_column(Text)  # только contracts.ml_contracts.normalize_text()
    embedding: Mapped[Any | None] = mapped_column(Vector(EMBEDDING_DIM), deferred=True)
    ktru_code: Mapped[str] = mapped_column(Text)
    source_item_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("items.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=_NOW)


# --- Качество -------------------------------------------------------------------------------------------


class EvalSample(Base):
    __tablename__ = "eval_samples"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    split: Mapped[str] = mapped_column(Text)
    raw_text: Mapped[str] = mapped_column(Text)
    expected_ktru_code: Mapped[str] = mapped_column(Text)
    expected_attrs: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    difficulty: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str | None] = mapped_column(Text)


class EvalRun(Base):
    __tablename__ = "eval_runs"

    id: Mapped[uuid.UUID] = _id()
    split: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default=text("'running'"))
    config: Mapped[dict[str, Any]] = mapped_column(JSONB)
    metrics: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=_NOW)


# Представление «итоговый код позиции» — единственное место правила «решение пользователя, иначе rank 1».
# Отдельные метаданные: представление нельзя создавать как таблицу.
views = MetaData()
v_item_final = Table(
    "v_item_final",
    views,
    Column("item_id", Uuid),
    Column("job_id", Uuid),
    Column("ktru_code", Text),
    Column("name", Text),
    Column("source", Text),
    Column("confidence", REAL),
)
