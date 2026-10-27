"""Готовность ML-воркера для GET /health и режим для GET /config.

worker-ml после загрузки моделей раз в ML_HEARTBEAT_INTERVAL_S пишет состояние в Redis с TTL ML_READY_TTL_S.
Воркер остановлен или завис — ключ истекает, и /health показывает «система готовится».
"""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Callable
from typing import Any, Protocol

log = logging.getLogger(__name__)

READY_KEY = "ktru:ml:ready"


class KeyValueStore(Protocol):
    """То, что нужно от клиента Redis: так в тестах его можно заменить словарём."""

    def set(self, name: str, value: str, ex: int | None = None) -> Any: ...

    def get(self, name: str) -> Any: ...

    def delete(self, *names: str) -> Any: ...


def write_status(store: KeyValueStore, status: dict[str, Any], ttl_s: int) -> None:
    store.set(READY_KEY, json.dumps(status, ensure_ascii=False), ex=ttl_s)


def read_status(store: KeyValueStore) -> dict[str, Any] | None:
    raw = store.get(READY_KEY)
    if raw is None:
        return None
    try:
        status = json.loads(raw)
    except (TypeError, ValueError):
        log.warning("В Redis лежит нечитаемое состояние ML-воркера")
        return None
    return status if isinstance(status, dict) else None


class Heartbeat:
    """Фоновый поток: сразу и затем раз в interval_s секунд пишет payload() в Redis."""

    def __init__(self, store: KeyValueStore, payload: Callable[[], dict[str, Any]], interval_s: int, ttl_s: int):
        self._store = store
        self._payload = payload
        self._interval_s = interval_s
        self._ttl_s = ttl_s
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="ml-heartbeat", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self, *, clear: bool = True) -> None:
        self._stop.set()
        self._thread.join(timeout=self._interval_s + 1)
        if clear:
            try:
                self._store.delete(READY_KEY)
            except Exception:
                log.warning("Не удалось убрать состояние ML-воркера из Redis", exc_info=True)

    def beat(self) -> None:
        try:
            write_status(self._store, self._payload(), self._ttl_s)
        except Exception:
            # Redis недоступен: ключ истечёт сам, /health покажет «не готово». Поток не падает.
            log.warning("Не удалось записать состояние ML-воркера в Redis", exc_info=True)

    def _run(self) -> None:
        self.beat()
        while not self._stop.wait(self._interval_s):
            self.beat()
