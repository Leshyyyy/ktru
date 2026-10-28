"""Связь с ML: выбор конвейера, режим работы для /config и проверка ответа по контракту ml_contracts.py.

Из ml/ бэкенд берёт только ml.pipeline.get_pipeline() (правило ml/CLAUDE.md).
"""

from __future__ import annotations

import importlib.util
import logging
import math
import os
from collections.abc import Container, Mapping, Sequence
from itertools import pairwise

from contracts.ml_contracts import ClassificationResult, Pipeline, RawItem

from .settings import Settings

log = logging.getLogger(__name__)

EPS = 1e-6  # допуск на округление float: 1.0000001 — это ещё 1, а 1.01 — уже ошибка конвейера
# ML округляет каждую уверенность до 4 знаков после нормирования, поэтому сумма бывает 1.0001
# (три значения × 0.00005). Это шум, а не нарушение контракта: позицию из-за него не роняем.
SUM_EPS = 1e-3
_EXTRACTORS = ("rules", "llm", "hybrid")
_RERANKERS = ("none", "cross_encoder", "llm")
_DEVICES = ("cpu", "cuda")


CATALOG_FALLBACK = (
    "Каталог КТРУ не скачался — работает демонстрационная заглушка с кодами 99.99.*. "
    "Проверьте доступ к huggingface.co и запустите заново: docker compose up."
)


def catalog_fallback(settings: Settings) -> str | None:
    """PIPELINE=real, а файла каталога нет (migrate не смог его скачать) → причина отката на заглушку.

    migrate и worker-ml решают по одному признаку — есть ли файл KTRU_CATALOG_PATH, — поэтому оба откатываются
    вместе: migrate загружает каталог-фикстуру, worker-ml отвечает её кодами.
    """
    if settings.pipeline != "real" or settings.ktru_catalog_path is None:
        return None
    return None if settings.ktru_catalog_path.is_file() else CATALOG_FALLBACK


def load_pipeline(settings: Settings) -> Pipeline:
    """Загружает модели. Вызывать один раз на процесс воркера: это может занять минуты."""
    # Импорт внутри функции: ml.pipeline тянет numpy и scikit-learn, а нужен только worker-ml.
    if settings.pipeline == "stub":
        from contracts.stub_pipeline import StubPipeline

        return StubPipeline()
    from ml.pipeline import get_pipeline

    return get_pipeline()


def pipeline_mode(pipeline: object | None, settings: Settings) -> dict[str, object]:
    """Режим для GET /config, который worker-ml кладёт в heartbeat. Конвейер, который умеет сообщить режим сам
    (describe_mode() — необязательный метод, не часть контракта), знает его точнее: умолчания не дублируются.
    """
    describe = getattr(pipeline, "describe_mode", None)
    if callable(describe):
        try:
            return dict(describe())
        except Exception:
            log.exception("Pipeline.describe_mode() упал, режим — по переменным окружения")
    return describe_mode(settings)


def describe_mode(settings: Settings, environ: Mapping[str, str] = os.environ) -> dict[str, object]:
    """Режим по переменным окружения — когда конвейер не сообщает его сам. Умолчания и llm_enabled — как
    в ml/config.py (EXTRACTOR=rules, RERANKER=none). API берёт режим у worker-ml через Redis: только у него
    есть ключ LLM; сам считает его, лишь пока воркера нет.
    """
    if settings.pipeline == "stub":
        return {"extractor": "rules", "reranker": "none", "device": "cpu", "llm_enabled": False}
    reranker = _choice(environ, "RERANKER", _RERANKERS, "none")
    has_key = bool(environ.get("LLM_API_KEY", "").strip())
    has_url = bool(environ.get("LLM_BASE_URL", "").strip())
    llm_enabled = has_key and (has_url or reranker == "llm")
    # Те же откаты, что в ml/rerank.py make_reranker: без ключа LLM-реранкера нет, а cross_encoder
    # без sentence-transformers работает как none. /config показывает фактический режим.
    if (reranker == "llm" and not has_key) or (reranker == "cross_encoder" and not _installed("sentence_transformers")):
        reranker = "none"
    return {
        "extractor": _choice(environ, "EXTRACTOR", _EXTRACTORS, "rules"),
        "reranker": reranker,
        "device": _choice(environ, "DEVICE", _DEVICES, "cpu"),
        "llm_enabled": llm_enabled,
    }


def _installed(module: str) -> bool:
    return importlib.util.find_spec(module) is not None


def _choice(environ: Mapping[str, str], name: str, allowed: tuple[str, ...], default: str) -> str:
    value = environ.get(name, "").strip() or default
    if value not in allowed:
        log.warning("Неизвестное значение переменной, беру по умолчанию", extra={"var": name, "default": default})
        return default
    return value


def check_results(
    items: Sequence[RawItem],
    results: Sequence[ClassificationResult],
    known_codes: Container[str] | None = None,
) -> list[ClassificationResult]:
    """Проверяет ответ Pipeline.classify_batch по правилам контракта и возвращает по результату на позицию.

    Нарушение превращается в ошибку позиции (status = failed), а не в исключение: иначе одна уверенность
    1.0000001 нарушит CHECK в predictions, уронит транзакцию пачки и отправит её в бесконечные ретраи.
    known_codes — коды версии каталога задачи: код rank 1 обязан в ней быть, альтернативы без кода отбрасываются.
    """
    by_id: dict[str, ClassificationResult] = {}
    for result in results:
        if result.item_id in by_id:
            log.warning("ML вернул два результата для одной позиции", extra={"item_id": result.item_id})
            continue
        by_id[result.item_id] = result
    expected = {item.item_id for item in items}
    if extra := set(by_id) - expected:
        log.warning("ML вернул результаты для чужих позиций", extra={"count": len(extra)})

    checked = []
    for item in items:
        result = by_id.get(item.item_id)
        if result is None:
            checked.append(_failed(item.item_id, "ML не вернул результат для позиции."))
        else:
            checked.append(_check_one(result, known_codes))
    return checked


def _failed(item_id: str, error: str) -> ClassificationResult:
    return ClassificationResult(item_id=item_id, error=error)


def _invalid(result: ClassificationResult, reason: str) -> ClassificationResult:
    log.error("Некорректный ответ ML", extra={"item_id": result.item_id, "reason": reason})
    return result.model_copy(update={"predictions": [], "error": f"Некорректный ответ ML: {reason}."})


def _check_one(result: ClassificationResult, known_codes: Container[str] | None) -> ClassificationResult:
    if result.error:
        return result.model_copy(update={"predictions": []})
    predictions = list(result.predictions)
    if not predictions:
        return _invalid(result, "не предложено ни одного кода")
    if len(predictions) > 3 or [p.rank for p in predictions] != list(range(1, len(predictions) + 1)):
        return _invalid(result, "ранги должны идти подряд от 1 до 3")

    confidences = []
    for prediction in predictions:
        value = prediction.confidence
        if not math.isfinite(value) or not -EPS <= value <= 1 + EPS:
            return _invalid(result, "уверенность вне диапазона 0..1")
        confidences.append(min(max(value, 0.0), 1.0))
    if any(lower > higher + EPS for higher, lower in pairwise(confidences)):
        return _invalid(result, "уверенность растёт с рангом")
    if sum(confidences) > 1 + SUM_EPS:
        return _invalid(result, "сумма уверенностей больше 1")

    fixed = [p.model_copy(update={"confidence": c}) for p, c in zip(predictions, confidences, strict=True)]
    if known_codes is not None:
        if fixed[0].ktru_code not in known_codes:
            return _invalid(result, f"кода {fixed[0].ktru_code} нет в версии каталога задачи")
        kept = [p for p in fixed if p.ktru_code in known_codes]
        if len(kept) < len(fixed):
            log.warning("Отброшены альтернативы не из каталога", extra={"item_id": result.item_id})
        fixed = [p.model_copy(update={"rank": rank}) for rank, p in enumerate(kept, start=1)]
    return result.model_copy(update={"predictions": fixed})
