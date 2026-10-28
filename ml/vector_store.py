"""Эмбеддинги каталога и подтверждённых примеров в Postgres (pgvector, contracts/schema.sql).

catalog_items.embedding — vector(EMBEDDING_DIM). Заполняет его воркер ML (в схеме: «embedding пишет не бэкенд»):
fill_catalog считает векторы для позиций версии с embedding IS NULL, catalog_vectors отдаёт их конвейеру.
Векторы версии воркер читает один раз при загрузке версии и ищет по ним в памяти: на пачке позиций умножение
матриц быстрее, чем запрос к HNSW-индексу на каждую позицию. Индекс (schema.sql) остаётся для поиска из SQL.

Модель может давать вектор короче колонки (e5-small — 384): он дополняется нулями. Косинусная близость и порядок
соседей от этого не меняются, а схема и индекс остаются одними для любой модели.
"""

from __future__ import annotations

import logging
from typing import Optional

import numpy as np

log = logging.getLogger(__name__)

BATCH = 256


def pad(vecs: np.ndarray, dim: int) -> np.ndarray:
    """Нормированные векторы → длина dim (нули в хвосте не меняют косинус)."""
    vecs = np.asarray(vecs, dtype=np.float32)
    if vecs.shape[1] > dim:
        raise ValueError(f"Эмбеддер даёт {vecs.shape[1]} измерений, а колонка vector({dim})")
    if vecs.shape[1] == dim:
        return vecs
    return np.hstack([vecs, np.zeros((vecs.shape[0], dim - vecs.shape[1]), dtype=np.float32)])


class PgVectorStore:
    def __init__(self, database_url: str, dim: int = 1024):
        import psycopg  # noqa: PLC0415
        from pgvector.psycopg import register_vector  # noqa: PLC0415

        # SQLAlchemy-стиль «postgresql+psycopg://» → обычный DSN libpq.
        self.dsn = database_url.replace("postgresql+psycopg://", "postgresql://")
        self.dim = dim
        self._psycopg = psycopg
        self._register = register_vector

    def _connect(self):
        conn = self._psycopg.connect(self.dsn, autocommit=True)
        self._register(conn)
        return conn

    def latest_version(self) -> Optional[str]:
        """Последняя готовая версия каталога в базе — её загрузил migrate из того же файла, что читает воркер."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id FROM catalog_versions WHERE status = 'ready' ORDER BY loaded_at DESC LIMIT 1"
            ).fetchone()
        return str(row[0]) if row else None

    def fill_catalog(self, version_id: str, embedder, kind: str = "passage") -> int:
        """Векторы наименований позиций версии каталога, где embedding ещё пуст. Одинаковые наименования
        считаются один раз (в КТРУ 54 тыс. позиций, но ~13 тыс. разных наименований). → число обновлённых строк."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, name FROM catalog_items WHERE version_id = %s AND embedding IS NULL", (version_id,)
            ).fetchall()
            if not rows:
                return 0
            ids_of: dict[str, list] = {}
            for row_id, name in rows:
                ids_of.setdefault(name, []).append(row_id)
            names = sorted(ids_of)
            # Порциями: каждая сразу пишется в базу (autocommit), и перезапуск воркера посреди первого расчёта
            # не начинает его заново — пересчитаются только строки, где embedding ещё пуст.
            with conn.cursor() as cur:
                for start in range(0, len(names), BATCH):
                    chunk = names[start:start + BATCH]
                    vecs = pad(np.asarray(embedder.embed(chunk, kind=kind)), self.dim)
                    cur.executemany("UPDATE catalog_items SET embedding = %s WHERE id = %s",
                                    [(v, row_id) for name, v in zip(chunk, vecs) for row_id in ids_of[name]])
            log.info("Эмбеддинги каталога %s: %d позиций, %d наименований", version_id, len(rows), len(names))
            return len(rows)

    def catalog_vectors(self, version_id: str) -> dict[str, np.ndarray]:
        """ktru_code → вектор (обрезанный до исходной длины не нужен: хвост нулевой)."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT ktru_code, embedding FROM catalog_items WHERE version_id = %s AND embedding IS NOT NULL",
                (version_id,),
            ).fetchall()
        # pgvector-python отдаёт Vector (to_numpy) или список — смотря по версии.
        return {code: np.asarray(vec.to_numpy() if hasattr(vec, "to_numpy") else vec, dtype=np.float32)
                for code, vec in rows}


def connect(database_url: Optional[str], dim: int) -> Optional[PgVectorStore]:
    """Хранилище, если задан DATABASE_URL и установлен psycopg; иначе None (векторы считаются в памяти)."""
    if not database_url:
        return None
    try:
        return PgVectorStore(database_url, dim)
    except ImportError:
        log.warning("DATABASE_URL задан, но psycopg/pgvector не установлены — векторы в памяти")
        return None
