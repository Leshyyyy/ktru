"""Обработка задачи в воркерах (раздел 6 аналитики): разбор файла, пачки через ML, итоговый статус, watchdog.

Каждый переход статуса — один UPDATE с условием на текущий статус. Поэтому повторная доставка задачи Celery,
два воркера на одной пачке, отмена или удаление задачи посреди работы не ломают её состояние: опоздавший
воркер не находит строку в нужном статусе и тихо завершается. Позиции пишутся только из status = 'pending'.
"""

from __future__ import annotations

import logging
import math
import struct
import uuid
from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Any, Literal

from celery.exceptions import SoftTimeLimitExceeded
from sqlalchemy import Engine, and_, case, delete, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from backend.files import FileParsingError, ParsedRow, parse_rows
from contracts.ml_contracts import ClassificationResult, Pipeline, PipelineOptions, RawItem
from contracts.ml_contracts import Prediction as MlPrediction

from .ml import check_results
from .models import CatalogItem, ConfirmedExample, Item, Job, Prediction, SourceFile
from .queue import TaskQueue
from .settings import Settings
from .uploads import file_error

log = logging.getLogger(__name__)

CHUNK_SIZE = 50
STALE_AFTER = timedelta(minutes=5)
EXAMPLES_OVERLAP = timedelta(minutes=1)  # см. ExampleFeed
ACTIVE_STATUSES = ("queued", "parsing", "processing")
JOB_ERROR_CODES = ("file_unreadable", "mapping_invalid", "limit_exceeded", "internal_error")
NOTHING_PROCESSED = "Не удалось обработать ни одной позиции. Повторите обработку позже."
_PREDICTION_FIELDS = ("ktru_code", "confidence", "features", "characteristic_matches", "explanation", "why_not_chosen")

Band = Literal["green", "yellow", "red"]


# --- Цвет уверенности ---------------------------------------------------------------------------------------


def as_real(value: float) -> float:
    """Число так, как его хранит колонка real (float4). Уверенность 0.7 в базе — 0.69999999: без этого статус,
    посчитанный воркером, и цвет, посчитанный API по значению из базы, разошлись бы ровно на пороге.
    """
    return struct.unpack("f", struct.pack("f", value))[0]


def band_of(confidence: float, settings: Settings) -> Band:
    value = as_real(confidence)
    if value >= as_real(settings.green_threshold):
        return "green"
    return "yellow" if value >= as_real(settings.yellow_threshold) else "red"


def item_status(confidence: float, settings: Settings) -> str:
    """processed — уверенность rank 1 не ниже порога green, иначе needs_review (ItemStatus в openapi.yaml)."""
    return "processed" if band_of(confidence, settings) == "green" else "needs_review"


# --- io.parse_file --------------------------------------------------------------------------------------------


class _JobFailed(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def parse_job(engine: Engine, settings: Settings, queue: TaskQueue, job_id: uuid.UUID) -> None:
    """Файл по column_mapping → items, задача → processing, позиции пачками по 50 → очередь ml.

    Разбор идёт вне транзакции: большой pdf не держит блокировку задачи. Повторный запуск (watchdog, повторная
    доставка) безопасен: позиции создаются в одной транзакции с переходом parsing → processing.
    """
    with Session(engine) as session, session.begin():
        started = session.execute(
            update(Job)
            .where(Job.id == job_id, Job.status.in_(("queued", "parsing")))
            .values(status="parsing", started_at=func.coalesce(Job.started_at, func.now()))
            .returning(Job.file_id, Job.column_mapping)
        ).first()
        if started is None:
            log.info("Разбор пропущен: задачи нет или она уже не ждёт разбора")
            return
        storage_path = session.scalar(select(SourceFile.storage_path).where(SourceFile.id == started.file_id))

    try:
        rows = _parse(settings, storage_path, started.column_mapping)
    except _JobFailed as e:
        with Session(engine) as session, session.begin():
            session.execute(
                update(Job)
                .where(Job.id == job_id, Job.status == "parsing")
                .values(status="failed", error=e.message, error_code=e.code, finished_at=func.now())
            )
        log.warning("Файл задачи не разобран", extra={"error_code": e.code})
        return

    item_ids = _create_items(engine, job_id, rows)
    enqueue_chunks(queue, job_id, item_ids)


def _parse(settings: Settings, storage_path: str | None, mapping: dict[str, Any]) -> list[ParsedRow]:
    path = settings.upload_dir / storage_path if storage_path else None
    if path is None or not path.is_file():
        raise _JobFailed("internal_error", "Загруженный файл не найден на сервере. Загрузите его заново.")
    try:
        rows = parse_rows(path, mapping.get("sheet"), mapping.get("header_row", 0), mapping["columns"])
    except FileParsingError as e:
        code, message, _ = file_error(e)
        if code not in JOB_ERROR_CODES:
            code = "limit_exceeded" if code == "file_too_large" else "file_unreadable"
        raise _JobFailed(code, message) from e
    except SoftTimeLimitExceeded as e:
        raise _JobFailed("limit_exceeded", "Файл разбирается слишком долго. Разбейте его на части.") from e
    except Exception as e:
        log.exception("Разбор файла упал с неожиданной ошибкой")
        raise _JobFailed("internal_error", "Не удалось разобрать файл из-за внутренней ошибки.") from e
    if len(rows) > settings.max_items_per_job:
        raise _JobFailed(
            "limit_exceeded",
            f"Позиций в файле: {len(rows)}, а в одной задаче — не больше {settings.max_items_per_job}. "
            "Разбейте файл на части.",
        )
    return rows


def _create_items(engine: Engine, job_id: uuid.UUID, rows: Sequence[ParsedRow]) -> list[str]:
    with Session(engine) as session, session.begin():
        status = session.scalar(select(Job.status).where(Job.id == job_id).with_for_update())
        if status != "parsing":
            log.info("Задачу отменили или удалили во время разбора файла")
            return []
        values = [
            {
                "id": uuid.uuid4(),
                "job_id": job_id,
                "row_index": row.row_index,
                "raw_text": row.raw_text,
                "raw_row": row.raw_row,
            }
            for row in rows
        ]
        session.execute(insert(Item), values)
        session.execute(update(Job).where(Job.id == job_id).values(status="processing", total_items=len(values)))
    log.info("Файл разобран", extra={"items": len(values)})
    return [str(v["id"]) for v in values]


def enqueue_chunks(queue: TaskQueue, job_id: uuid.UUID, item_ids: Sequence[str]) -> None:
    for start in range(0, len(item_ids), CHUNK_SIZE):
        queue.process_chunk(str(job_id), list(item_ids[start : start + CHUNK_SIZE]))


# --- ml.process_chunk -----------------------------------------------------------------------------------------


def raw_columns(mapping: dict[str, Any], raw_row: Sequence[Any]) -> dict[str, str]:
    """RawItem.columns: значения по ролям колонок; несколько description или characteristics — слева направо."""
    parts: dict[str, list[str]] = {}
    for column in sorted(mapping.get("columns", []), key=lambda c: c["index"]):
        role, index = column["role"], column["index"]
        if role == "ignore" or index >= len(raw_row):
            continue
        if value := str(raw_row[index] or "").strip():
            parts.setdefault(role, []).append(value)
    return {role: " ".join(values) for role, values in parts.items()}


class ExampleFeed:
    """Подтверждённые примеры для конвейера (FR-4.7): перед пачкой досылает в Pipeline.add_examples пары
    «текст → код» из confirmed_examples, появившиеся с прошлого раза. Один экземпляр на процесс worker-ml.

    Отметка — created_at последнего отправленного примера; смена кода примера сдвигает created_at (routes/items.py).
    created_at = now() — время начала транзакции, поэтому пример может появиться в таблице позже соседа с бо́льшим
    created_at. Окно EXAMPLES_OVERLAP присылает такие пары повторно: повтор текста в конвейере заменяет код.
    """

    def __init__(self) -> None:
        self.since: datetime | None = None

    def push(self, engine: Engine, pipeline: Pipeline) -> int:
        add_examples = getattr(pipeline, "add_examples", None)  # не часть контракта: у заглушки его нет
        if add_examples is None:
            return 0
        query = select(ConfirmedExample.text, ConfirmedExample.ktru_code, ConfirmedExample.created_at)
        if self.since is not None:
            query = query.where(ConfirmedExample.created_at > self.since - EXAMPLES_OVERLAP)
        with engine.connect() as conn:
            rows = conn.execute(query.order_by(ConfirmedExample.created_at, ConfirmedExample.id)).all()
        if rows:
            # По возрастанию created_at: у одинаковых текстов побеждает последнее решение.
            add_examples([(row.text, row.ktru_code) for row in rows])
            self.since = rows[-1].created_at
        return len(rows)


def process_chunk(
    engine: Engine,
    settings: Settings,
    pipeline: Pipeline,
    job_id: uuid.UUID,
    item_ids: Sequence[uuid.UUID],
    examples: ExampleFeed | None = None,
) -> None:
    """Пачка → Pipeline.classify_batch → items и predictions. Исключение конвейера уходит наверх: ретраи
    делает задача Celery, а после последней попытки позиции помечает fail_chunk.
    """
    with Session(engine) as session:
        job = session.get(Job, job_id)
        if job is None or job.status != "processing" or job.cancel_requested:
            log.info("Пачка пропущена: задача удалена, отменена или уже завершена")
            return
        version_id, mapping, options = job.catalog_version_id, job.column_mapping, job.options
        items = session.execute(
            select(Item.id, Item.raw_text, Item.raw_row)
            .where(Item.job_id == job_id, Item.id.in_(item_ids), Item.status == "pending")
            .order_by(Item.row_index)
        ).all()
    if not items:
        # Пачку уже записала прошлая попытка; если на ней задача и закончилась — статус ставится здесь.
        finish_if_complete(engine, job_id)
        return

    if examples is not None:
        examples.push(engine, pipeline)
    raw = [RawItem(item_id=str(i.id), text=i.raw_text, columns=raw_columns(mapping, i.raw_row)) for i in items]
    results = pipeline.classify_batch(
        raw,
        str(version_id),
        PipelineOptions(use_llm=options.get("use_llm", True), use_feedback=options.get("use_feedback", True)),
    )
    catalog = _catalog_statuses(engine, version_id, results)
    _save_results(engine, settings, job_id, check_results(raw, results, known_codes=catalog), catalog)


def _catalog_statuses(engine: Engine, version_id: uuid.UUID, results: Sequence[ClassificationResult]) -> dict[str, str]:
    """Коды из ответа ML, которые есть в версии каталога задачи, и их статус (active / archived)."""
    codes = {p.ktru_code for r in results for p in r.predictions}
    if not codes:
        return {}
    with engine.connect() as conn:
        rows = conn.execute(
            select(CatalogItem.ktru_code, CatalogItem.status).where(
                CatalogItem.version_id == version_id, CatalogItem.ktru_code.in_(codes)
            )
        )
        return dict(rows.all())


def _save_results(
    engine: Engine,
    settings: Settings,
    job_id: uuid.UUID,
    results: Sequence[ClassificationResult],
    catalog: dict[str, str],
) -> None:
    processed = failed = 0
    degraded = any(r.mode == "degraded" for r in results)
    with Session(engine) as session, session.begin():
        for result in results:
            if _save_item(session, settings, job_id, result, catalog):
                if result.error:
                    failed += 1
                else:
                    processed += 1
        _add_counters(session, job_id, processed=processed, failed=failed, degraded=degraded)
        _finish_if_complete(session, job_id)
    log.info("Пачка обработана", extra={"processed": processed, "failed": failed})


def _save_item(
    session: Session, settings: Settings, job_id: uuid.UUID, result: ClassificationResult, catalog: dict[str, str]
) -> bool:
    """Записывает результат позиции; False — позиция уже не pending (её записала другая попытка)."""
    values: dict[str, Any] = {
        "extracted": result.extracted.model_dump(mode="json") if result.extracted else None,
        "path": result.path,
        "timings_ms": result.timings_ms or None,
        "processed_at": func.now(),
    }
    if result.error:
        values.update(status="failed", error=result.error, flags=[], norm_key=None, group_id=None)
    else:
        top = result.predictions[0]
        flags = list(dict.fromkeys(result.flags))
        if catalog.get(top.ktru_code) == "archived" and "archived_code" not in flags:
            flags.append("archived_code")
        values.update(
            status=item_status(top.confidence, settings),
            error=None,
            flags=flags,
            norm_key=result.norm_key,
            # Группа повторов: одинаковый ключ в задаче → один group_id (комментарий к items в schema.sql).
            group_id=uuid.uuid5(job_id, result.norm_key) if result.norm_key else None,
        )
    item_id = uuid.UUID(result.item_id)
    updated = session.execute(
        update(Item)
        .where(Item.id == item_id, Item.job_id == job_id, Item.status == "pending")
        .values(**values)
        .returning(Item.id)
    ).first()
    if updated is None:
        return False
    if not result.error:
        _save_predictions(session, item_id, result.predictions)
    return True


def _save_predictions(session: Session, item_id: uuid.UUID, predictions: Sequence[MlPrediction]) -> None:
    rows = [
        {
            "item_id": item_id,
            "rank": p.rank,
            "ktru_code": p.ktru_code,
            "confidence": p.confidence,
            # NaN и бесконечность не записать в jsonb, а калибратору такие признаки не нужны.
            "features": {k: v for k, v in p.features.items() if math.isfinite(v)},
            "characteristic_matches": [m.model_dump(mode="json") for m in p.characteristic_matches],
            "explanation": p.explanation,
            "why_not_chosen": None if p.rank == 1 else p.why_not_chosen,
        }
        for p in predictions
    ]
    stmt = insert(Prediction).values(rows)
    session.execute(
        stmt.on_conflict_do_update(
            index_elements=[Prediction.item_id, Prediction.rank],
            set_={name: stmt.excluded[name] for name in _PREDICTION_FIELDS},
        )
    )
    session.execute(delete(Prediction).where(Prediction.item_id == item_id, Prediction.rank > len(rows)))


def fail_chunk(engine: Engine, job_id: uuid.UUID, item_ids: Sequence[uuid.UUID], message: str) -> None:
    """Пачка не прошла после всех ретраев: её pending-позиции → failed, задача продолжается."""
    with Session(engine) as session, session.begin():
        if session.scalar(select(Job.status).where(Job.id == job_id)) != "processing":
            return
        failed = session.scalars(
            update(Item)
            .where(Item.job_id == job_id, Item.id.in_(item_ids), Item.status == "pending")
            .values(status="failed", error=message, processed_at=func.now())
            .returning(Item.id)
        ).all()
        _add_counters(session, job_id, processed=0, failed=len(failed), degraded=False)
        _finish_if_complete(session, job_id)
    log.error("Позиции пачки помечены как ошибочные", extra={"failed": len(failed)})


def _add_counters(session: Session, job_id: uuid.UUID, *, processed: int, failed: int, degraded: bool) -> None:
    # Счётчики растут атомарно (x = x + n): пачки одной задачи пишут их параллельно.
    values: dict[str, Any] = {}
    if processed:
        values["processed_items"] = Job.processed_items + processed
    if failed:
        values["failed_items"] = Job.failed_items + failed
    if degraded:
        values["effective_mode"] = "degraded"
    if values:
        session.execute(update(Job).where(Job.id == job_id).values(**values))


# --- Итоговый статус (maybe_finalize) -------------------------------------------------------------------------


def _finish_if_complete(session: Session, job_id: uuid.UUID) -> None:
    """processed + failed = total → done, done_with_errors или failed (ни одна позиция не обработана).

    Вызывается в транзакции пачки, после её счётчиков: строку задачи держит блокировка UPDATE, так что
    из двух одновременно закончившихся пачек статус поставит та, что увидит полные счётчики.
    """
    nothing_processed = and_(Job.processed_items == 0, Job.failed_items > 0)
    status = session.scalar(
        update(Job)
        .where(
            Job.id == job_id,
            Job.status == "processing",
            Job.processed_items + Job.failed_items >= Job.total_items,
        )
        .values(
            status=case(
                (Job.failed_items == 0, "done"), (Job.processed_items == 0, "failed"), else_="done_with_errors"
            ),
            error=case((nothing_processed, NOTHING_PROCESSED), else_=None),
            error_code=case((nothing_processed, "internal_error"), else_=None),
            finished_at=func.now(),
        )
        .returning(Job.status)
    )
    if status is not None:
        log.info("Задача завершена", extra={"status": status})


def finish_if_complete(engine: Engine, job_id: uuid.UUID) -> None:
    with Session(engine) as session, session.begin():
        _finish_if_complete(session, job_id)


# --- watchdog -------------------------------------------------------------------------------------------------


def requeue_stale_jobs(engine: Engine, queue: TaskQueue, stale_after: timedelta = STALE_AFTER) -> int:
    """Задачи в queued, parsing или processing без обновлений дольше stale_after: разбор или pending-позиции
    ставятся в очередь заново (воркер упал, брокер потерял сообщение). Возвращает число таких задач.
    """
    with Session(engine) as session, session.begin():
        stale = session.execute(
            select(Job.id, Job.status)
            .where(Job.status.in_(ACTIVE_STATUSES), Job.updated_at < func.now() - stale_after)
            .with_for_update(skip_locked=True)
        ).all()
        if not stale:
            return 0
        # Отметка времени (её ставит триггер): к этой задаче watchdog вернётся не раньше чем через stale_after.
        session.execute(update(Job).where(Job.id.in_([job.id for job in stale])).values(updated_at=func.now()))
        pending = {
            job.id: [
                str(item_id)
                for item_id in session.scalars(
                    select(Item.id).where(Item.job_id == job.id, Item.status == "pending").order_by(Item.row_index)
                )
            ]
            for job in stale
            if job.status == "processing"
        }

    for job in stale:
        log.warning(
            "Задача без движения — ставлю в очередь заново", extra={"job_id": str(job.id), "status": job.status}
        )
        if job.status != "processing":
            queue.parse_file(str(job.id))
        elif pending[job.id]:
            enqueue_chunks(queue, job.id, pending[job.id])
        else:
            finish_if_complete(engine, job.id)
    return len(stale)
