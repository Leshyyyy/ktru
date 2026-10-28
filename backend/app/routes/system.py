"""Тег system: GET /health, GET /config и GET /stats."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.core.ml import describe_mode
from backend.core.settings import Settings

from ..deps import get_db, get_health_checker, get_ml_status, get_settings
from ..health import HealthChecker
from ..schemas import Health, Limits, Mode, PublicConfig, Stats, Thresholds
from ..stats import stats

router = APIRouter(tags=["system"])


@router.get("/health", response_model=Health)
def health(checker: Annotated[HealthChecker, Depends(get_health_checker)]) -> Health:
    return checker.check()


@router.get("/config", response_model=PublicConfig)
def config(
    settings: Annotated[Settings, Depends(get_settings)],
    ml_status: Annotated[dict[str, Any] | None, Depends(get_ml_status)],
) -> PublicConfig:
    # Режим сообщает worker-ml: только у него есть ключ LLM. Пока воркера нет — режим по переменным
    # окружения API, без ключа (llm_enabled = false: позиции во внешний API ещё не уходят).
    reported = (ml_status or {}).get("mode")
    try:
        mode = Mode.model_validate(reported) if reported else Mode.model_validate(describe_mode(settings))
    except ValueError:
        mode = Mode.model_validate(describe_mode(settings))
    return PublicConfig(
        limits=Limits(
            max_file_mb=settings.max_file_mb,
            max_rows=settings.max_rows,
            max_items_per_job=settings.max_items_per_job,
            classify_max_items=settings.classify_max_items,
        ),
        thresholds=Thresholds(green=settings.green_threshold, yellow=settings.yellow_threshold),
        mode=mode,
    )


@router.get("/stats", response_model=Stats)
def get_stats(
    settings: Annotated[Settings, Depends(get_settings)], session: Annotated[Session, Depends(get_db)]
) -> Stats:
    return stats(session, settings)
