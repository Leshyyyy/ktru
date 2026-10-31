"""Ответы API о задачах и позициях: схемы Job и Item из таблиц. На страницу — несколько запросов, без N+1.

Итоговый код позиции берётся только из представления v_item_final. Цвет (band) — по уверенности rank 1 и порогам
из /config, решения пользователя его не меняют (описание summary в openapi.yaml).
"""

from __future__ import annotations

import math
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import REAL, and_, cast, func, literal, select
from sqlalchemy.orm import Session, aliased

from backend.core import models
from backend.core.processing import band_of
from backend.core.settings import Settings

from . import schemas

_EMPTY_SUMMARY = schemas.JobSummary(green=0, yellow=0, red=0, confirmed=0, corrected=0)


def real_literal(value: float) -> Any:
    # Порог сравнивается с колонкой real как real: иначе 0.7 из базы (0.69999999) оказался бы ниже порога 0.7.
    return cast(literal(value), REAL)


def _utc(value: datetime | None) -> datetime | None:
    return value.astimezone(UTC) if value is not None else None


# --- Задачи ---------------------------------------------------------------------------------------------------


def job_views(session: Session, settings: Settings, job_ids: Sequence[uuid.UUID]) -> list[schemas.Job]:
    """Задачи в порядке job_ids; несуществующие пропускаются."""
    if not job_ids:
        return []
    rows = session.execute(
        select(models.Job, models.SourceFile.filename)
        .join(models.SourceFile, models.SourceFile.id == models.Job.file_id)
        .where(models.Job.id.in_(job_ids))
    ).all()
    found = {job.id: (job, filename) for job, filename in rows}
    summaries = _summaries(session, settings, list(found))
    now = datetime.now(UTC)
    views = []
    for job_id in job_ids:
        if job_id in found:
            job, filename = found[job_id]
            views.append(_job(job, filename, summaries.get(job_id, _EMPTY_SUMMARY), now))
    return views


def _summaries(
    session: Session, settings: Settings, job_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, schemas.JobSummary]:
    if not job_ids:
        return {}
    top = aliased(models.Prediction)
    green, yellow = real_literal(settings.green_threshold), real_literal(settings.yellow_threshold)
    rows = session.execute(
        select(
            models.Item.job_id,
            func.count().filter(top.confidence >= green).label("green"),
            func.count().filter(top.confidence >= yellow, top.confidence < green).label("yellow"),
            func.count().filter(top.confidence < yellow).label("red"),
            func.count().filter(models.Item.status == "confirmed").label("confirmed"),
            func.count().filter(models.Item.status == "corrected").label("corrected"),
        )
        .outerjoin(top, and_(top.item_id == models.Item.id, top.rank == 1))
        .where(models.Item.job_id.in_(job_ids))
        .group_by(models.Item.job_id)
    ).all()
    return {
        row.job_id: schemas.JobSummary(
            green=row.green, yellow=row.yellow, red=row.red, confirmed=row.confirmed, corrected=row.corrected
        )
        for row in rows
    }


def _job(job: models.Job, filename: str, summary: schemas.JobSummary, now: datetime) -> schemas.Job:
    done = job.processed_items + job.failed_items
    total = job.total_items
    return schemas.Job(
        job_id=job.id,
        status=job.status,
        file_id=job.file_id,
        filename=filename,
        total_items=total,
        processed_items=job.processed_items,
        failed_items=job.failed_items,
        progress=round(min(done / total, 1.0), 3) if total else 0.0,
        eta_seconds=_eta(job, done, now),
        effective_mode=job.effective_mode,
        catalog_version_id=job.catalog_version_id,
        summary=summary,
        error=job.error,
        error_code=job.error_code,
        created_at=_utc(job.created_at),
        started_at=_utc(job.started_at),
        finished_at=_utc(job.finished_at),
    )


def _eta(job: models.Job, done: int, now: datetime) -> int | None:
    """По скорости этой задачи с начала разбора; до первой обработанной пачки оценки нет."""
    if job.status != "processing" or not done or job.started_at is None:
        return None
    elapsed = (now - job.started_at).total_seconds()
    if elapsed <= 0:
        return None
    return math.ceil(max(job.total_items - done, 0) * elapsed / done)


# --- Позиции --------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ItemFilters:
    band: schemas.Band | None = None
    status: schemas.ItemStatus | None = None
    q: str | None = None
    flag: schemas.ItemFlag | None = None
    group_id: uuid.UUID | None = None
    sort: str = "row_index"


def item_page(
    session: Session, settings: Settings, job_id: uuid.UUID, filters: ItemFilters, page: int, page_size: int
) -> schemas.ItemList:
    top = aliased(models.Prediction)
    query = (
        select(models.Item.id)
        .outerjoin(top, and_(top.item_id == models.Item.id, top.rank == 1))
        .where(models.Item.job_id == job_id, *_conditions(filters, top, settings))
    )
    total = session.scalar(select(func.count()).select_from(query.subquery())) or 0
    if filters.sort == "confidence_asc":
        # Позиции без предсказаний (ошибка, ещё не обработана) — сверху: их тоже нужно посмотреть.
        order = (top.confidence.asc().nulls_first(), models.Item.row_index)
    else:
        order = (models.Item.row_index,)
    ids = session.scalars(query.order_by(*order).limit(page_size).offset((page - 1) * page_size)).all()
    return schemas.ItemList(items=item_views(session, settings, ids), total=total, page=page, page_size=page_size)


def _conditions(filters: ItemFilters, top: Any, settings: Settings) -> list[Any]:
    conditions: list[Any] = []
    if filters.status:
        conditions.append(models.Item.status == filters.status)
    if filters.band:
        green, yellow = real_literal(settings.green_threshold), real_literal(settings.yellow_threshold)
        conditions.append(
            {
                "green": top.confidence >= green,
                "yellow": and_(top.confidence >= yellow, top.confidence < green),
                "red": top.confidence < yellow,
            }[filters.band]
        )
    if filters.q and (q := filters.q.strip()):
        conditions.append(models.Item.raw_text.ilike(f"%{escape_like(q)}%", escape="\\"))
    if filters.flag:
        conditions.append(models.Item.flags.contains([filters.flag]))
    if filters.group_id:
        conditions.append(models.Item.group_id == filters.group_id)
    return conditions


def escape_like(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def item_views(session: Session, settings: Settings, item_ids: Sequence[uuid.UUID]) -> list[schemas.Item]:
    """Позиции в порядке item_ids со всеми предсказаниями, итоговым кодом и размером группы повторов."""
    if not item_ids:
        return []
    items = {item.id: item for item in session.scalars(select(models.Item).where(models.Item.id.in_(item_ids)))}
    predictions, top_confidence = _predictions(session, list(items))
    finals = {
        row.item_id: row
        for row in session.execute(select(models.v_item_final).where(models.v_item_final.c.item_id.in_(list(items))))
    }
    sizes = _group_sizes(session, list(items.values()))
    views = []
    for item_id in item_ids:
        if item := items.get(item_id):
            size = sizes.get(item.group_id, 1) if item.group_id else 1
            confidence = top_confidence.get(item_id)
            band = band_of(confidence, settings) if confidence is not None else None
            views.append(_item(item, predictions.get(item_id, []), band, finals.get(item_id), size))
    return views


def _predictions(
    session: Session, item_ids: Sequence[uuid.UUID]
) -> tuple[dict[uuid.UUID, list[schemas.Prediction]], dict[uuid.UUID, float]]:
    """Предсказания по позициям и уверенность rank 1 как она лежит в базе: цвет считается по ней, а не по
    округлённой для ответа — иначе на пороге он разошёлся бы со статусом, который поставил воркер.
    Наименование и ОКПД2 — из версии каталога, с которой работает задача (как в v_item_final).
    """
    rows = session.execute(
        select(models.Prediction, models.CatalogItem.name, models.CatalogItem.okpd2_code)
        .join(models.Item, models.Item.id == models.Prediction.item_id)
        .join(models.Job, models.Job.id == models.Item.job_id)
        .outerjoin(
            models.CatalogItem,
            and_(
                models.CatalogItem.version_id == models.Job.catalog_version_id,
                models.CatalogItem.ktru_code == models.Prediction.ktru_code,
            ),
        )
        .where(models.Prediction.item_id.in_(item_ids))
        .order_by(models.Prediction.item_id, models.Prediction.rank)
    ).all()
    result: dict[uuid.UUID, list[schemas.Prediction]] = {}
    top_confidence: dict[uuid.UUID, float] = {}
    for prediction, name, okpd2_code in rows:
        if prediction.rank == 1:
            top_confidence[prediction.item_id] = prediction.confidence
        result.setdefault(prediction.item_id, []).append(
            schemas.Prediction(
                rank=prediction.rank,
                ktru_code=prediction.ktru_code,
                okpd2_code=okpd2_code or prediction.ktru_code.split("-", 1)[0],
                name=name or prediction.ktru_code,
                confidence=round(prediction.confidence, 4),
                explanation=prediction.explanation,
                why_not_chosen=prediction.why_not_chosen,
                characteristic_matches=prediction.characteristic_matches,
            )
        )
    return result, top_confidence


def _group_sizes(session: Session, items: Sequence[models.Item]) -> dict[uuid.UUID, int]:
    groups = {item.group_id for item in items if item.group_id}
    if not groups:
        return {}
    jobs = {item.job_id for item in items}
    rows = session.execute(
        select(models.Item.group_id, func.count())
        .where(models.Item.job_id.in_(jobs), models.Item.group_id.in_(groups))
        .group_by(models.Item.group_id)
    )
    return dict(rows.all())


def _item(
    item: models.Item, predictions: list[schemas.Prediction], band: str | None, final: Any, group_size: int
) -> schemas.Item:
    return schemas.Item(
        item_id=item.id,
        job_id=item.job_id,
        row_index=item.row_index,
        raw_text=item.raw_text,
        status=item.status,
        band=band,
        path=item.path,
        flags=item.flags,
        # group_id = null, если в группе одна позиция (openapi.yaml, Item.group_id).
        group_id=item.group_id if group_size > 1 else None,
        group_size=group_size,
        extracted=item.extracted,
        predictions=predictions,
        final=_final(final),
        error=item.error,
    )


def _final(row: Any) -> schemas.FinalAnswer | None:
    # ktru_code = NULL — ни предсказания, ни решения (pending или failed): итогового ответа нет.
    if row is None or row.ktru_code is None:
        return None
    confidence = round(row.confidence, 4) if row.confidence is not None else None
    return schemas.FinalAnswer(ktru_code=row.ktru_code, name=row.name, source=row.source, confidence=confidence)
