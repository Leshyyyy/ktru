"""GET /health: состояние базы, Redis, моделей и каталога по отдельности (FR-2.6).

ready = все компоненты в порядке. Пока ready ложно, фронт показывает «система готовится», а POST /jobs
отвечает 503 not_ready. Сообщения — для человека; подробности ошибок только в логе.
"""

from __future__ import annotations

import logging
from typing import Any

from redis import Redis
from redis.exceptions import RedisError
from sqlalchemy import Engine, select
from sqlalchemy.exc import SQLAlchemyError

from backend.core.models import CatalogVersion
from backend.core.readiness import read_status

from .errors import plural
from .schemas import ComponentHealth, Health, HealthComponents

log = logging.getLogger(__name__)

NO_DATABASE = "Нет связи с базой данных."


class HealthChecker:
    def __init__(self, engine: Engine, redis: Redis):
        self.engine = engine
        self.redis = redis

    def check(self) -> Health:
        database = self._database()
        redis, ml_status = self._redis()
        components = HealthComponents(
            database=database,
            redis=redis,
            models=_models(ml_status, redis_ok=redis.ok),
            catalog=self._catalog(ml_status, database_ok=database.ok),
        )
        ready = all(c.ok for c in (components.database, components.redis, components.models, components.catalog))
        return Health(ready=ready, components=components)

    def _database(self) -> ComponentHealth:
        try:
            with self.engine.connect() as conn:
                conn.exec_driver_sql("SELECT 1")
        except SQLAlchemyError:
            log.warning("Проверка базы данных не прошла", exc_info=True)
            return ComponentHealth(ok=False, detail=NO_DATABASE)
        return ComponentHealth(ok=True)

    def _redis(self) -> tuple[ComponentHealth, dict[str, Any] | None]:
        try:
            self.redis.ping()
            return ComponentHealth(ok=True), read_status(self.redis)
        except RedisError:
            log.warning("Проверка Redis не прошла", exc_info=True)
            return ComponentHealth(ok=False, detail="Нет связи с Redis."), None

    def _catalog(self, ml_status: dict[str, Any] | None, *, database_ok: bool) -> ComponentHealth:
        if not database_ok:
            return ComponentHealth(ok=False, detail=NO_DATABASE)
        try:
            with self.engine.connect() as conn:
                row = conn.execute(
                    select(CatalogVersion.id, CatalogVersion.items_count)
                    .where(CatalogVersion.status == "ready")
                    .order_by(CatalogVersion.loaded_at.desc())
                    .limit(1)
                ).first()
        except SQLAlchemyError:
            log.warning("Не удалось прочитать версию каталога", exc_info=True)
            return ComponentHealth(ok=False, detail=NO_DATABASE)
        if row is None:
            return ComponentHealth(ok=False, detail="Каталог КТРУ не загружен.")
        if row.items_count <= 0:
            return ComponentHealth(ok=False, detail="Каталог КТРУ пуст.")
        if ml_status is not None and not ml_status.get("catalog", False):
            return ComponentHealth(ok=False, detail="ML-воркер ещё не загрузил каталог.")
        count = f"{row.items_count:,}".replace(",", " ")
        detail = f"версия {row.id.hex[:8]}, {count} {plural(row.items_count, 'позиция', 'позиции', 'позиций')}"
        return ComponentHealth(ok=True, detail=detail)


def _models(ml_status: dict[str, Any] | None, *, redis_ok: bool) -> ComponentHealth:
    if not redis_ok:
        return ComponentHealth(ok=False, detail="Состояние ML-воркера неизвестно: нет связи с Redis.")
    if ml_status is None:
        return ComponentHealth(ok=False, detail="ML-воркер не запущен или ещё загружает модели.")
    if error := ml_status.get("error"):
        return ComponentHealth(ok=False, detail=str(error))
    if not ml_status.get("models", False):
        return ComponentHealth(ok=False, detail="Модели ещё загружаются.")
    if ml_status.get("pipeline") == "stub":
        return ComponentHealth(ok=True, detail="Заглушка StubPipeline (PIPELINE=stub), коды 99.99.*.")
    return ComponentHealth(ok=True)
