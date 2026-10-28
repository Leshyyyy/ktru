"""Поиск кандидатов КТРУ в памяти: векторные эмбеддинги (EMBEDDER_MODEL) + TF-IDF + нечёткое совпадение названия
+ обязательные характеристики.

Эмбеддинги каталога считаются один раз и кэшируются на диске (EMBED_CACHE_DIR): ключ кэша — модель и хеш текстов,
поэтому новая версия каталога пересчитывается сама. Без EMBEDDER_MODEL работает только TF-IDF (без скачивания моделей).

Режим pgvector (catalog_items в Postgres) — следующий шаг, когда у команды будет база;
интерфейс тот же — Retriever.search_batch из contracts/ml_contracts.py.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Optional

import numpy as np
from rapidfuzz import fuzz, process
from sklearn.feature_extraction.text import TfidfVectorizer

from contracts.ml_contracts import Candidate, SearchQuery, normalize_text

from .catalog import Catalog, CatalogEntry
from .category import query_text
from .charmatch import CharMatcher, build_query_context

log = logging.getLogger(__name__)

# Веса в объединённой оценке поиска. С эмбеддером: смысловая близость + буквенная (TF-IDF) + название.
W_VECTOR = 0.55
W_NAME = 0.45
# С эмбеддером (EMBEDDER_MODEL) вес смысловой близости малый: на ste_dev / v3 dev e5-small с весом 0,35 давал
# 77,7 / 79,6% против 78,0 / 83,1% без него, с весом 0,10 — 77,7 / 81,9%. Категорию уже даёт ml.category.
W_DENSE, W_TFIDF, W_DENSE_NAME = 0.10, 0.45, 0.45
# TF-IDF: доля индекса по одному наименованию КТРУ, остальное — индекс наименования с характеристиками.
# На живых названиях индекс по наименованию сильнее, на строках с характеристиками — полный (ml_probe).
TFIDF_NAME_SHARE = 0.5
PREFILTER_GAP = 0.25  # в предварительный отбор идут позиции не хуже лучшей минус этот зазор
PREFILTER_MAX = 400

# Категории ОКПД2 от ml.category: кандидаты берутся внутри CAT_TOP лучших категорий (пока их суммарная доля
# голосов не дойдёт до CAT_MASS), доля категории входит в оценку с весом W_CAT. Из всего каталога добавляется
# FALLBACK_N лучших по тексту — для кодов, которых нет среди карточек СТЕ (≈19% разметки v3), и для ошибок модели.
# Доля входит в степени CAT_POWER (у модели с голосом близость⁶ доли и так резкие — степень 1).
# Подобрано на ste_dev и v3 dev (ml/CLAUDE.md).
CAT_TOP = 3
CAT_MASS = 0.95
W_CAT = 0.4
CAT_POWER = 1.0
FALLBACK_N = 15


class MemoryRetriever:
    def __init__(self, catalog: Catalog, matcher: CharMatcher, embedder=None, cache_dir: str | Path | None = None,
                 vectors: Optional[dict[str, np.ndarray]] = None):
        """vectors — готовые эмбеддинги позиций (ktru_code → вектор) из pgvector; иначе считаются здесь."""
        self.items: list[CatalogEntry] = catalog.searchable
        self.matcher = matcher
        self.embedder = embedder
        self.names = [e.name for e in self.items]
        texts = [e.search_text() for e in self.items]
        self.vectorizer = TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 5), sublinear_tf=True)
        self.matrix = self.vectorizer.fit_transform(texts) if texts else None
        self.name_vectorizer = TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 5), sublinear_tf=True)
        self.name_matrix = self.name_vectorizer.fit_transform(self.names) if texts else None
        self.okpd2 = [e.okpd2_code for e in self.items]
        by_okpd: dict[str, list[int]] = {}
        for j, code in enumerate(self.okpd2):
            by_okpd.setdefault(code, []).append(j)
        self.by_okpd = {code: np.array(js) for code, js in by_okpd.items()}
        # Эмбеддинги — по наименованиям: одинаковых наименований в КТРУ много (54 тыс. позиций, ~13 тыс. наименований),
        # а характеристики в векторе только размывают смысл (на живых названиях наименование точнее).
        self.uniq_names = sorted(set(self.names))
        pos = {n: i for i, n in enumerate(self.uniq_names)}
        self.name_idx = np.array([pos[n] for n in self.names], dtype=np.int64)
        self.dense = None
        if embedder is not None and texts:
            self.dense = self._from_vectors(vectors) if vectors else None
            if self.dense is None:
                self.dense = self._catalog_embeddings(self.uniq_names, cache_dir)
        # Подтверждённые пользователями пары «нормализованный текст → код» (кэш точных совпадений).
        self.examples: dict[str, str] = {}
        self._index = {e.ktru_code: i for i, e in enumerate(self.items)}

    def add_examples(self, pairs: dict[str, str]) -> None:
        self.examples.update({normalize_text(t): code for t, code in pairs.items()})

    def _from_vectors(self, vectors: dict[str, np.ndarray]) -> Optional[np.ndarray]:
        """Матрица по наименованиям из векторов позиций; None, если векторов не хватает (тогда считаем сами)."""
        by_name = {}
        for e in self.items:
            v = vectors.get(e.ktru_code)
            if v is not None:
                by_name.setdefault(e.name, v)
        if len(by_name) < len(self.uniq_names):
            return None
        # В базе вектор дополнен нулями до vector(1024) — обрезаем до длины, которую даёт модель.
        dim = getattr(self.embedder, "dim", None) or len(self.embedder.embed(["x"], kind="query")[0])
        return np.vstack([np.asarray(by_name[n], dtype=np.float32)[:dim] for n in self.uniq_names])

    def _catalog_embeddings(self, texts: list[str], cache_dir: str | Path | None) -> np.ndarray:
        digest = hashlib.sha1("\n".join(texts).encode("utf-8")).hexdigest()[:16]
        slug = self.embedder.model_name.replace("/", "__")
        path = Path(cache_dir) / f"catalog_{slug}_{digest}.npy" if cache_dir else None
        if path and path.exists():
            return np.load(path)
        log.info("Считаю эмбеддинги каталога: %d наименований, модель %s", len(texts), self.embedder.model_name)
        dense = np.asarray(self.embedder.embed(texts, kind="passage"), dtype=np.float32)
        if path:
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                np.save(path, dense)
            except OSError as e:
                log.warning("Не удалось сохранить кэш эмбеддингов (%s)", e)
        return dense

    def _vector_scores(self, texts: list[str]) -> tuple[np.ndarray, Optional[np.ndarray]]:
        """→ (TF-IDF косинус, косинус эмбеддингов или None). Эмбеддинг запроса — по наименованию без характеристик."""
        full = (self.vectorizer.transform(texts) @ self.matrix.T).toarray()
        name = (self.name_vectorizer.transform(texts) @ self.name_matrix.T).toarray()
        tfidf = TFIDF_NAME_SHARE * name + (1 - TFIDF_NAME_SHARE) * full
        if self.dense is None:
            return tfidf, None
        q = np.asarray(self.embedder.embed([query_text(t) for t in texts], kind="query"), dtype=np.float32)
        return tfidf, np.clip(q @ self.dense.T, 0.0, 1.0)[:, self.name_idx]

    def search_batch(self, queries: list[SearchQuery], use_feedback: bool = True,
                     categories: Optional[list[dict[str, float]]] = None) -> list[list[Candidate]]:
        """categories — доли ОКПД2 от модели категорий для каждого запроса (или None — искать по всему каталогу)."""
        if not queries or not self.items:
            return [[] for _ in queries]
        tfidf, dense = self._vector_scores([q.text for q in queries])
        name_queries = [q.extracted.name or q.text for q in queries]
        name = process.cdist(name_queries, self.names, scorer=fuzz.token_set_ratio, workers=-1) / 100.0
        if dense is None:
            vec = tfidf
            fused = W_VECTOR * tfidf + W_NAME * name
        else:
            vec = dense  # vector_score в контракте — косинус эмбеддингов
            fused = W_DENSE * dense + W_TFIDF * tfidf + W_DENSE_NAME * name

        out = []
        for i, q in enumerate(queries):
            probs = categories[i] if categories else None
            row, pool = self._pool(fused[i], probs)
            qctx = build_query_context(q.text)
            # Предварительный порядок: поиск + обязательные характеристики (без них не различить одноимённые позиции).
            prelim = []
            for j in pool:
                req = self.matcher.match_entry(self.items[j], qctx, required_only=True)
                prelim.append((0.5 * row[j] + 0.5 * req.score, j))
            prelim.sort(reverse=True)
            chosen = [j for _, j in prelim[: q.top_k]]
            hit = self.examples.get(normalize_text(q.text)) if use_feedback else None
            if hit is not None and hit in self._index and self._index[hit] not in chosen:
                chosen = [self._index[hit]] + chosen[: q.top_k - 1]
            out.append([self._candidate(j, vec[i, j], name[i, j], row[j]) for j in chosen])
        return out

    def _pool(self, fused: np.ndarray, probs: Optional[dict[str, float]]) -> tuple[np.ndarray, np.ndarray]:
        """→ (оценка с учётом категории, индексы предварительного отбора по убыванию оценки)."""
        cats, mass = [], 0.0
        for code, p in (probs or {}).items():  # доли уже по убыванию
            if code in self.by_okpd:
                cats.append(code)
                mass += p
                if len(cats) >= CAT_TOP or mass >= CAT_MASS:
                    break
        if not cats:  # модели категорий нет или она не знает ни одной категории каталога — поиск по всему каталогу
            pool = np.flatnonzero(fused >= fused.max() - PREFILTER_GAP)
            return fused, pool[np.argsort(-fused[pool])][:PREFILTER_MAX]
        row = (1 - W_CAT) * fused
        inside = np.concatenate([self.by_okpd[c] for c in cats])
        for c in cats:
            row[self.by_okpd[c]] += W_CAT * probs[c] ** CAT_POWER
        inside = inside[row[inside] >= row[inside].max() - PREFILTER_GAP]
        inside = inside[np.argsort(-row[inside])][:PREFILTER_MAX]
        k = min(FALLBACK_N + len(inside), len(row) - 1)
        outside = np.argpartition(-row, k)[: k + 1]
        outside = [j for j in outside[np.argsort(-row[outside])] if self.okpd2[j] not in cats][:FALLBACK_N]
        pool = np.concatenate([inside, np.array(outside, dtype=inside.dtype)])
        return row, pool[np.argsort(-row[pool])]

    def example_vote(self, text: str, ktru_code: str) -> float:
        return 1.0 if self.examples.get(normalize_text(text)) == ktru_code else 0.0

    def entry(self, ktru_code: str) -> Optional[CatalogEntry]:
        j = self._index.get(ktru_code)
        return self.items[j] if j is not None else None

    def _candidate(self, j: int, v: float, t: float, f: float) -> Candidate:
        e = self.items[j]
        return Candidate(
            ktru_code=e.ktru_code,
            name=e.name,
            okpd2_code=e.okpd2_code,
            vector_score=float(v),
            text_score=float(t),
            fused_score=float(f),
            characteristics=e.raw_characteristics,
        )
