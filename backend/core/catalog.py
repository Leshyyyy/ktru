"""Каталог КТРУ в базе: импорт из JSONL (одна строка — CatalogRow) в catalog_versions и catalog_items,
выбор версии для новых задач.

Импортом будет работать POST /catalog/import (FR-8.1); сейчас его вызывает migrate для стартового каталога.
Файл полного каталога для PIPELINE=real migrate скачивает сам (download_catalog), если его ещё нет на томе.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path
from typing import Any

from pydantic import ValidationError
from sqlalchemy import Engine, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from contracts.ml_contracts import CatalogRow

from .models import CatalogItem, CatalogVersion

log = logging.getLogger(__name__)

BATCH_SIZE = 500
DOWNLOAD_TIMEOUT_S = 900
_UPDATABLE = (
    "okpd2_code",
    "name",
    "description",
    "unit",
    "status",
    "is_enlarged",
    "replaced_by",
    "characteristics",
    "search_text",
)


class CatalogImportError(Exception):
    """Файл каталога не подходит для импорта. Текст — для человека."""


def build_search_text(row: CatalogRow) -> str:
    """Текст для полнотекстового индекса (catalog_items.tsv): наименование, описание, названия характеристик
    и их текстовые значения.

    Числа не берём, как и ML в ml/catalog.py: внутри одного ОКПД2 позиции различаются числами, а их сравнивает
    сверка характеристик, а не текстовый поиск. CatalogIndexer у ML может пересобрать поле при индексации.
    """
    parts = [row.name]
    if row.description:
        parts.append(row.description)
    for characteristic in row.characteristics:
        name = characteristic.get("name")
        if isinstance(name, str):
            parts.append(name)
        for value in characteristic.get("values") or []:
            if isinstance(value, str) and value.strip() not in ("Да", "Нет") and not any(c.isdigit() for c in value):
                parts.append(value)
    return " ".join(" ".join(parts).split())


def read_rows(path: Path) -> Iterator[CatalogRow]:
    with path.open(encoding="utf-8") as f:
        for number, line in enumerate(f, start=1):
            if not line.strip():
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError as e:
                raise CatalogImportError(f"Строка {number}: некорректный JSON ({e.msg}).") from e
            try:
                yield CatalogRow.model_validate(data)
            except ValidationError as e:
                fields = ", ".join(".".join(str(p) for p in err["loc"]) or "строка" for err in e.errors())
                message = f"Строка {number}: не соответствует формату CatalogRow (поля: {fields})."
                raise CatalogImportError(message) from e


def download_catalog(path: Path, timeout_s: float = DOWNLOAD_TIMEOUT_S) -> bool:
    """Полный КТРУ через `python -m ml.fetch_catalog`: ревизия датасета и sha256 закреплены у ML.

    Пишет во временный файл рядом и переименовывает: оборванная загрузка не оставит «готовый» каталог.
    Отдельный процесс — чтобы не тянуть код ML в migrate и ограничить время. → удалось ли.
    """
    part = path.with_name(path.name + ".part")
    command = [sys.executable, "-m", "ml.fetch_catalog", "--out", str(part)]
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(command, check=True, timeout=timeout_s)  # noqa: S603 — фиксированная команда
        os.replace(part, path)
    except (OSError, subprocess.SubprocessError):
        log.exception("Не удалось скачать каталог КТРУ", extra={"path": str(path)})
        part.unlink(missing_ok=True)
        return False
    return True


def latest_ready_version(session: Session) -> uuid.UUID | None:
    """Версия, с которой работают новые задачи и поиск по умолчанию: последняя загруженная в статусе ready."""
    return session.scalar(
        select(CatalogVersion.id)
        .where(CatalogVersion.status == "ready")
        .order_by(CatalogVersion.loaded_at.desc())
        .limit(1)
    )


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def import_catalog_file(session: Session, path: Path, *, source: str, note: str | None = None) -> CatalogVersion:
    """Создаёт версию каталога и загружает строки в транзакции вызывающего: ошибка — откат целиком.
    Версия сразу ready — так migrate загружает стартовый каталог, который worker-ml читает из того же файла.
    """
    version = CatalogVersion(source=source, status="importing", note=note)
    session.add(version)
    session.flush()
    load_catalog_rows(session, version, path)
    version.status = "ready"
    session.flush()
    return version


def load_catalog_rows(session: Session, version: CatalogVersion, path: Path) -> int:
    """Строки файла → catalog_items версии, items_count. Статус версии не меняется. → число позиций.

    Повтор кода внутри файла обновляет строку (последняя побеждает), как и повторная загрузка позиции.
    """
    batch: dict[str, dict[str, Any]] = {}
    for row in read_rows(path):
        batch[row.ktru_code] = _values(version.id, row)
        if len(batch) >= BATCH_SIZE:
            _upsert(session, batch)
            batch = {}
    if batch:
        _upsert(session, batch)

    count = session.scalar(select(func.count()).select_from(CatalogItem).where(CatalogItem.version_id == version.id))
    if not count:
        raise CatalogImportError("В файле нет ни одной позиции каталога.")
    version.items_count = count
    session.flush()
    return count


def catalog_rows(session: Session, version_id: uuid.UUID) -> list[dict[str, Any]]:
    """Строки версии в формате CatalogRow (dict) — для Pipeline.load_version в worker-ml (FR-8.1)."""
    item = CatalogItem
    rows = session.execute(
        select(
            item.ktru_code,
            item.okpd2_code,
            item.name,
            item.description,
            item.unit,
            item.status,
            item.is_enlarged,
            item.replaced_by,
            item.characteristics,
        ).where(item.version_id == version_id)
    )
    return [dict(row._mapping) for row in rows]


def _values(version_id: uuid.UUID, row: CatalogRow) -> dict[str, Any]:
    return {
        "version_id": version_id,
        "ktru_code": row.ktru_code,
        "okpd2_code": row.okpd2_code,
        "name": row.name,
        "description": row.description,
        "unit": row.unit,
        "status": row.status,
        "is_enlarged": row.is_enlarged,
        "replaced_by": row.replaced_by,
        "characteristics": row.characteristics,
        "search_text": build_search_text(row),
    }


def _upsert(session: Session, batch: dict[str, dict[str, Any]]) -> None:
    stmt = insert(CatalogItem).values(list(batch.values()))
    stmt = stmt.on_conflict_do_update(
        index_elements=[CatalogItem.version_id, CatalogItem.ktru_code],
        set_={name: stmt.excluded[name] for name in _UPDATABLE},
    )
    session.execute(stmt)


# --- POST /catalog/import (FR-8.1) ------------------------------------------------------------------------------
#
# 1. API сохраняет файл и создаёт версию в статусе importing.
# 2. worker-io (io.import_catalog) загружает строки: items_count > 0, статус по-прежнему importing.
# 3. worker-ml (CatalogSync) замечает такую версию, в отдельном потоке строит её индекс (Pipeline.load_version:
#    модели, векторы в pgvector) и ставит ready. Пока строится, задачи обслуживает прежняя версия.
# Новые задачи закрепляются за последней ready (latest_ready_version) — значит, за версией, которую ML уже знает.

# Источник версии, которую загрузил migrate: её же worker-ml читает из KTRU_CATALOG_PATH при старте.
MIGRATE_SOURCE = "migrate:"
IMPORT_SOURCE = "api:"
STALE_IMPORT = timedelta(hours=2)
IMPORT_FAILED = "Импорт не выполнен — подробности в логах."


def run_catalog_import(engine: Engine, version_id: uuid.UUID, path: Path) -> None:
    """Задача io.import_catalog. Повторная доставка после успешной загрузки ничего не делает. Файл удаляется."""
    try:
        with Session(engine) as session, session.begin():
            version = session.get(CatalogVersion, version_id, with_for_update=True)
            if version is None or version.status != "importing" or version.items_count:
                return
            count = load_catalog_rows(session, version, path)
        log.info("Строки каталога загружены, ждём индекс ML", extra={"version_id": str(version_id), "items": count})
    except CatalogImportError as e:
        log.warning("Каталог не импортирован: %s", e, extra={"version_id": str(version_id)})
        fail_version(engine, version_id, str(e))
    except Exception:
        log.exception("Каталог не импортирован", extra={"version_id": str(version_id)})
        fail_version(engine, version_id, IMPORT_FAILED)
    finally:
        path.unlink(missing_ok=True)


def fail_version(engine: Engine, version_id: uuid.UUID, message: str) -> None:
    """importing → failed; причина дописывается к комментарию версии (в CatalogVersion другого поля для неё нет)."""
    with engine.begin() as conn:
        conn.execute(
            update(CatalogVersion)
            .where(CatalogVersion.id == version_id, CatalogVersion.status == "importing")
            .values(status="failed", note=func.concat_ws(" — ", CatalogVersion.note, f"Ошибка: {message}"))
        )


def fail_stale_imports(engine: Engine, stale_after: timedelta = STALE_IMPORT) -> int:
    """Импорт, застрявший в importing (воркер упал посреди загрузки или индексации) → failed. Для watchdog."""
    with engine.begin() as conn:
        stale = conn.scalars(
            select(CatalogVersion.id).where(
                CatalogVersion.status == "importing", CatalogVersion.loaded_at < func.now() - stale_after
            )
        ).all()
    for version_id in stale:
        fail_version(engine, version_id, "импорт прервался, загрузите файл заново.")
    return len(stale)


class CatalogSync:
    """worker-ml: подхватывает новую версию каталога без перезапуска (FR-8.1). poll() зовётся раз в несколько секунд.

    Загружается только последняя версия: миграционную (MIGRATE_SOURCE) конвейер уже прочитал из файла.
    Конвейер без load_version (заглушка) индекс не строит — загруженная версия сразу становится ready.
    """

    def __init__(self, engine: Engine, pipeline: Any):
        self.engine = engine
        self.pipeline = pipeline
        self.loading: str | None = None
        self._lock = threading.Lock()

    def poll(self) -> None:
        load_version = getattr(self.pipeline, "load_version", None)
        with Session(self.engine) as session:
            loaded_rows = (CatalogVersion.status == "ready") | (
                (CatalogVersion.status == "importing") & (CatalogVersion.items_count > 0)
            )
            newest = session.execute(
                select(CatalogVersion.id, CatalogVersion.status, CatalogVersion.source)
                .where(loaded_rows)
                .order_by(CatalogVersion.loaded_at.desc())
                .limit(1)
            ).first()
        if newest is None or newest.source.startswith(MIGRATE_SOURCE):
            return
        if load_version is None:
            self._mark_ready(newest.id)
            return
        version_id = str(newest.id)
        loaded = getattr(self.pipeline, "loaded_versions", lambda: [])()
        with self._lock:
            if version_id in loaded or self.loading is not None:
                return
            self.loading = version_id
        threading.Thread(target=self._load, args=(newest.id,), name="catalog-load", daemon=True).start()

    def _load(self, version_id: uuid.UUID) -> None:
        started = time.perf_counter()
        try:
            with Session(self.engine) as session:
                rows = catalog_rows(session, version_id)
            self.pipeline.load_version(str(version_id), rows)
            self._mark_ready(version_id)
            log.info(
                "Версия каталога загружена в ML",
                extra={
                    "version_id": str(version_id),
                    "items": len(rows),
                    "load_s": round(time.perf_counter() - started),
                },
            )
        except Exception:
            log.exception("ML не загрузил версию каталога", extra={"version_id": str(version_id)})
            fail_version(self.engine, version_id, "ML не смог построить индекс версии.")
        finally:
            with self._lock:
                self.loading = None

    def _mark_ready(self, version_id: uuid.UUID) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                update(CatalogVersion)
                .where(CatalogVersion.id == version_id, CatalogVersion.status == "importing")
                .values(status="ready")
            )
