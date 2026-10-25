"""Реранкеры для неуверенных позиций (путь rerank). Все — Reranker из contracts/ml_contracts.py.

none           — порядок не меняется (режим без моделей и ключей).
cross_encoder  — локальная модель sentence-transformers (нужен torch).
llm            — Jev (TypeSafe) через OpenRouter: choice среди кандидатов с вероятностями. Нужен LLM_API_KEY.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor

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

    def __init__(self, api_key: str, model: str, timeout: float = 10.0, workers: int = 8):
        import httpx  # noqa: PLC0415

        self.client = httpx.Client(timeout=timeout, headers={"Authorization": f"Bearer {api_key}"})
        self.model = model
        self.workers = workers

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
        try:
            r = self.client.post(self.URL, json=body)
            r.raise_for_status()
            probs = r.json()["answers"]["ktru"]["probabilities"]
            order = sorted(top, key=lambda c: -float(probs.get(c.ktru_code, 0.0)))
            return RerankResult(
                ordered_codes=[c.ktru_code for c in order],
                scores=[float(probs.get(c.ktru_code, 0.0)) for c in order],
            )
        except Exception as e:  # сеть, лимиты, формат ответа — не роняем позицию
            log.warning("Jev недоступен: %s", e)
            return fallback

    def rerank_batch(self, pairs, texts: list[str] | None = None) -> list[RerankResult]:
        texts = texts or [a.name for a, _ in pairs]
        with ThreadPoolExecutor(self.workers) as ex:
            return list(ex.map(lambda k: self._one(texts[k], pairs[k][1]), range(len(pairs))))


def make_reranker(settings) -> tuple[object, bool]:
    """→ (реранкер, degraded). degraded = нужен был LLM-реранкер, но ключа нет."""
    if settings.reranker == "llm":
        if settings.llm_api_key:
            return JevReranker(settings.llm_api_key, settings.jev_model), False
        log.warning("RERANKER=llm, но LLM_API_KEY не задан — работаю без реранкера")
        return NoneReranker(), True
    if settings.reranker == "cross_encoder":
        try:
            return CrossEncoderReranker(settings.cross_encoder_model, settings.device), False
        except ImportError:
            log.warning("sentence-transformers не установлен — cross_encoder недоступен, работаю без реранкера")
            return NoneReranker(), False
    return NoneReranker(), False
