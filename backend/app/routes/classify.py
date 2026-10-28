"""Тег classify: POST /classify — несколько строк синхронно, ничего не сохраняет (FR-9.3).

Модели загружены только в worker-ml, поэтому API ставит задачу ml.classify в очередь classify и ждёт ответа.
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.core.catalog import latest_ready_version
from backend.core.queue import ClassifyTimeout, TaskQueue
from backend.core.settings import Settings

from .. import schemas
from ..deps import get_db, get_health_checker, get_queue, get_settings
from ..errors import ApiError, not_ready
from ..health import HealthChecker

log = logging.getLogger(__name__)
router = APIRouter(tags=["classify"])

ML_BUSY = "ML-воркер сейчас занят или не отвечает. Повторите через минуту."


@router.post("/classify", response_model=schemas.ClassifyResponse)
def classify(
    body: schemas.ClassifyRequest,
    settings: Annotated[Settings, Depends(get_settings)],
    session: Annotated[Session, Depends(get_db)],
    queue: Annotated[TaskQueue, Depends(get_queue)],
    checker: Annotated[HealthChecker, Depends(get_health_checker)],
) -> schemas.ClassifyResponse:
    if len(body.items) > settings.classify_max_items:
        message = f"Некорректный запрос: не больше {settings.classify_max_items} строк за раз."
        raise ApiError(400, "invalid_request", message, {"field": "items", "max": settings.classify_max_items})
    if not checker.check().ready:
        raise not_ready()
    version_id = latest_ready_version(session)
    if version_id is None:
        raise not_ready()
    session.close()  # соединение с базой не держим, пока ждём worker-ml
    try:
        results = queue.classify([item.text for item in body.items], str(version_id), settings.classify_timeout_s)
    except ClassifyTimeout:
        log.warning("worker-ml не ответил на /classify", extra={"timeout_s": settings.classify_timeout_s})
        raise ApiError(503, "not_ready", ML_BUSY, headers={"Retry-After": "30"}) from None
    except Exception:
        # Брокер недоступен или задача упала в воркере (её исключение get() поднимает здесь).
        log.exception("POST /classify не выполнен")
        raise ApiError(503, "not_ready", ML_BUSY, headers={"Retry-After": "30"}) from None
    return schemas.ClassifyResponse(results=[schemas.ClassifyResult.model_validate(r) for r in results])
