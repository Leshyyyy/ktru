"""GET /stats: сводка по качеству и скорости — SQL-запросы по рабочим таблицам, отдельной системы метрик нет."""

from __future__ import annotations

from sqlalchemy import and_, func, select, union_all
from sqlalchemy.orm import Session

from backend.core import models
from backend.core.settings import Settings

from . import schemas
from .views import real_literal

RECENT_ERRORS = 10


def stats(session: Session, settings: Settings) -> schemas.Stats:
    jobs_total = session.scalar(select(func.count()).select_from(models.Job)) or 0
    items_total, failed = session.execute(
        select(func.count(), func.count().filter(models.Item.status == "failed")).select_from(models.Item)
    ).one()
    return schemas.Stats(
        jobs_total=jobs_total,
        items_total=items_total,
        green_correction_rate=_green_correction_rate(session, settings),
        avg_seconds_per_1000=_seconds_per_1000(session),
        failed_items_share=round(failed / items_total, 4) if items_total else 0.0,
        recent_errors=_recent_errors(session),
    )


def _green_correction_rate(session: Session, settings: Settings) -> float | None:
    """corrected / (confirmed_manual + corrected) среди зелёных по rank 1 позиций, последнее решение по которым
    принял человек (via = manual). Массовые подтверждения не считаются: они не проверка, а согласие с моделью.
    """
    decision = models.Decision
    ranked = select(
        decision.item_id,
        decision.kind,
        decision.via,
        func.row_number().over(partition_by=decision.item_id, order_by=decision.created_at.desc()).label("n"),
    ).subquery()
    latest = select(ranked).where(ranked.c.n == 1).subquery()
    total, corrected = session.execute(
        select(func.count(), func.count().filter(latest.c.kind == "correct"))
        .select_from(latest)
        .join(
            models.Prediction,
            and_(models.Prediction.item_id == latest.c.item_id, models.Prediction.rank == 1),
        )
        .where(latest.c.via == "manual", models.Prediction.confidence >= real_literal(settings.green_threshold))
    ).one()
    return round(corrected / total, 4) if total else None


def _seconds_per_1000(session: Session) -> float | None:
    """Время от создания до завершения задачи (очередь, разбор и ML) на 1000 позиций по всем завершённым задачам."""
    job = models.Job
    seconds, items = session.execute(
        select(func.sum(func.extract("epoch", job.finished_at - job.created_at)), func.sum(job.total_items)).where(
            job.status.in_(("done", "done_with_errors")), job.finished_at.is_not(None), job.total_items > 0
        )
    ).one()
    return round(float(seconds) / items * 1000, 1) if items else None


def _recent_errors(session: Session) -> list[schemas.RecentError]:
    """Последние ошибки: упавшие позиции (текст ошибки конвейера) и упавшие задачи (ошибка разбора и т. п.)."""
    item, job = models.Item, models.Job
    item_errors = select(
        item.job_id.label("job_id"),
        item.error.label("error"),
        func.coalesce(item.processed_at, item.created_at).label("at"),
    ).where(item.status == "failed", item.error.is_not(None))
    job_errors = select(
        job.id.label("job_id"),
        job.error.label("error"),
        func.coalesce(job.finished_at, job.updated_at).label("at"),
    ).where(job.status == "failed", job.error.is_not(None))
    errors = union_all(item_errors, job_errors).subquery()
    rows = session.execute(select(errors).order_by(errors.c.at.desc()).limit(RECENT_ERRORS)).all()
    return [schemas.RecentError(job_id=r.job_id, error=r.error, at=r.at) for r in rows]
