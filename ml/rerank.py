"""Реранкеры для неуверенных позиций (путь rerank). Все — Reranker из contracts/ml_contracts.py.

none           — порядок не меняется (режим без моделей и ключей).
cross_encoder  — локальная модель sentence-transformers (нужен torch).
llm            — Jev (TypeSafe) через OpenRouter: choice среди кандидатов с вероятностями. Нужен LLM_API_KEY.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from contracts.ml_contracts import Candidate, ExtractedAttributes, RerankResult

log = logging.getLogger(__name__)

RERANK_TOP = 10


def _describe(c: Candidate, limit: int = 300) -> str:
    req = [f"{ch['name']}: {', '.join(map(str, ch.get('values', [])[:3]))}" for ch in c.characteristics if ch.get("required")]
    return (c.name + ("; " + "; ".join(req) if req else ""))[:limit]


class NoneReranker:
    name = "none"

    def rerank_batch(self, pairs, texts=None) -> list[RerankResult]:
        return [RerankResult(ordered_codes=[c.ktru_code for c in cands], scores=[0.0] * len(cands)) for _, cands in pairs]


class CrossEncoderReranker:
    name = "cross_encoder"

    def __init__(self, model_name: str, device: str = "cpu"):
        from sentence_transformers import CrossEncoder  # noqa: PLC0415

        self.model = CrossEncoder(model_name, device=device)

    def rerank_batch(self, pairs: list[tuple[ExtractedAttributes, list[Candidate]]], texts: list[str] | None = None):
        out = []
        for k, (attrs, cands) in enumerate(pairs):
            query = texts[k] if texts else attrs.name
            top = cands[:RERANK_TOP]
            scores = self.model.predict([(query, _describe(c)) for c in top]) if top else []
            order = sorted(zip(top, map(float, scores)), key=lambda x: -x[1])
            out.append(RerankResult(ordered_codes=[c.ktru_code for c, _ in order], scores=[s for _, s in order]))
        return out


class JevReranker:
    """Выбор кода из кандидатов моделью Jev. При любой ошибке — исходный порядок, позиция не падает."""

    name = "llm"
    URL = "https://openrouter.ai/api/alpha/decisions"

    def __init__(self, api_key: str, model: str, timeout: float = 10.0, workers: int = 8,
                 cache_path: str | Path | None = None):
        import httpx  # noqa: PLC0415

        self.client = httpx.Client(timeout=timeout, headers={"Authorization": f"Bearer {api_key}"})
        self.model = model
        self.workers = workers
        # Кэш ответов на диске: повторный прогон и «Повторить» не платят за те же вопросы дважды.
        self.cache_path = Path(cache_path) if cache_path else None
        self._cache: dict[str, dict] = {}
        self._lock = threading.Lock()
        if self.cache_path and self.cache_path.exists():
            for line in self.cache_path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    rec = json.loads(line)
                    self._cache[rec["key"]] = rec["probs"]

    def _one(self, text: str, cands: list[Candidate]) -> RerankResult:
        top = cands[:RERANK_TOP]
        fallback = RerankResult(ordered_codes=[c.ktru_code for c in top], scores=[0.0] * len(top))
        if len(top) < 2:
            return fallback
        body = {
            "model": self.model,
            "state": text,
            "questions": {
                "ktru": {
                    "type": "choice",
                    "instructions": "Какая позиция каталога КТРУ точнее всего описывает товар? "
                    "Сравни наименование и обязательные характеристики.",
                    "criteria": {c.ktru_code: _describe(c) for c in top},
                }
            },
        }
        key = hashlib.sha256(json.dumps(body, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        try:
            probs = self._cache.get(key)
            if probs is None:
                r = self.client.post(self.URL, json=body)
                r.raise_for_status()
                probs = r.json()["answers"]["ktru"]["probabilities"]
                self._remember(key, probs)
            order = sorted(top, key=lambda c: -float(probs.get(c.ktru_code, 0.0)))
            return RerankResult(
                ordered_codes=[c.ktru_code for c in order],
                scores=[float(probs.get(c.ktru_code, 0.0)) for c in order],
            )
        except Exception as e:  # сеть, лимиты, формат ответа — не роняем позицию
            log.warning("Jev недоступен: %s", e)
            return fallback

    def _remember(self, key: str, probs: dict) -> None:
        with self._lock:
            self._cache[key] = probs
            if not self.cache_path:
                return
            try:
                self.cache_path.parent.mkdir(parents=True, exist_ok=True)
                with open(self.cache_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps({"key": key, "probs": probs}, ensure_ascii=False) + "\n")
            except OSError as e:
                # В контейнере файловая система только для чтения: кэш остаётся в памяти, ответ Jev не теряем.
                log.warning("Кэш Jev не записан на диск (%s), дальше только в памяти", e)
                self.cache_path = None

    def rerank_batch(self, pairs, texts: list[str] | None = None) -> list[RerankResult]:
        texts = texts or [a.name for a, _ in pairs]
        with ThreadPoolExecutor(self.workers) as ex:
            return list(ex.map(lambda k: self._one(texts[k], pairs[k][1]), range(len(pairs))))


def effective_reranker(settings) -> str:
    """Какой реранкер на самом деле работает при этих настройках — те же откаты, что в make_reranker,
    но без загрузки моделей (для describe_mode до загрузки каталога)."""
    if settings.reranker == "llm":
        return "llm" if settings.llm_api_key else "none"
    if settings.reranker == "cross_encoder":
        import importlib.util  # noqa: PLC0415

        return "cross_encoder" if importlib.util.find_spec("sentence_transformers") else "none"
    return "none"


def make_reranker(settings) -> tuple[object, bool]:
    """→ (реранкер, degraded). degraded = нужен был LLM-реранкер, но ключа нет."""
    if settings.reranker == "llm":
        if settings.llm_api_key:
            return JevReranker(settings.llm_api_key, settings.jev_model, cache_path=settings.llm_cache_path), False
        log.warning("RERANKER=llm, но LLM_API_KEY не задан — работаю без реранкера")
        return NoneReranker(), True
    if settings.reranker == "cross_encoder":
        try:
            return CrossEncoderReranker(settings.cross_encoder_model, settings.device), False
        except ImportError:
            log.warning("sentence-transformers не установлен — cross_encoder недоступен, работаю без реранкера")
            return NoneReranker(), False
    return NoneReranker(), False
