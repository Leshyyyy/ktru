"""Поиск кандидатов КТРУ в памяти: вектор (TF-IDF или эмбеддер) + нечёткое совпадение названия + обязательные характеристики.

Режим pgvector (catalog_items в Postgres) — следующий шаг, когда у команды будет база;
интерфейс тот же — Retriever.search_batch из contracts/ml_contracts.py.
"""

from __future__ import annotations

from typing import Optional

import numpy as np
from rapidfuzz import fuzz, process
from sklearn.feature_extraction.text import TfidfVectorizer

from contracts.ml_contracts import Candidate, SearchQuery, normalize_text

from .catalog import Catalog, CatalogEntry
from .charmatch import CharMatcher, build_query_context

W_VECTOR = 0.55
W_NAME = 0.45
PREFILTER_GAP = 0.25  # в предварительный отбор идут позиции не хуже лучшей минус этот зазор
PREFILTER_MAX = 400


class MemoryRetriever:
    def __init__(self, catalog: Catalog, matcher: CharMatcher, embedder=None):
        self.items: list[CatalogEntry] = catalog.searchable
        self.matcher = matcher
        self.embedder = embedder
        self.names = [e.name for e in self.items]
        texts = [e.search_text() for e in self.items]
        if embedder is not None:
            self.matrix = np.asarray(embedder.embed(texts, kind="passage"), dtype=np.float32)
            self.vectorizer = None
        else:
            self.vectorizer = TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 5), sublinear_tf=True)
            self.matrix = self.vectorizer.fit_transform(texts) if texts else None
        # Подтверждённые пользователями пары «нормализованный текст → код» (кэш точных совпадений).
        self.examples: dict[str, str] = {}
        self._index = {e.ktru_code: i for i, e in enumerate(self.items)}

    def add_examples(self, pairs: dict[str, str]) -> None:
        self.examples.update({normalize_text(t): code for t, code in pairs.items()})

    def _vector_scores(self, texts: list[str]) -> np.ndarray:
        if self.vectorizer is not None:
            q = self.vectorizer.transform(texts)
            return (q @ self.matrix.T).toarray()
        q = np.asarray(self.embedder.embed(texts, kind="query"), dtype=np.float32)
        return np.clip(q @ self.matrix.T, 0.0, 1.0)

    def search_batch(self, queries: list[SearchQuery], use_feedback: bool = True) -> list[list[Candidate]]:
        if not queries or not self.items:
            return [[] for _ in queries]
        vec = self._vector_scores([q.text for q in queries])
        name_queries = [q.extracted.name or q.text for q in queries]
        name = process.cdist(name_queries, self.names, scorer=fuzz.token_set_ratio, workers=-1) / 100.0
        fused = W_VECTOR * vec + W_NAME * name

        out = []
        for i, q in enumerate(queries):
            row = fused[i]
            pool = np.flatnonzero(row >= row.max() - PREFILTER_GAP)
            pool = pool[np.argsort(-row[pool])][:PREFILTER_MAX]
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
