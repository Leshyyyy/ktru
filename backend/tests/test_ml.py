"""Адаптер ML: выбор конвейера, режим для /config и проверка ответа по контракту."""

from __future__ import annotations

import pytest

from backend.core import ml as ml_adapter
from backend.core.ml import check_results, describe_mode, load_pipeline
from backend.core.settings import Settings
from contracts.ml_contracts import ClassificationResult, Prediction, RawItem
from contracts.stub_pipeline import FAKE_CATALOG, StubPipeline

FAKE_CODES = {code for code, _, _ in FAKE_CATALOG}


def _items(n: int) -> list[RawItem]:
    texts = ["Ноутбук Lenovo IdeaPad 3 15IAU7, 16 ГБ", "Бумага офисная А4", "__fail__ проверка", "Перчатки нитриловые"]
    return [RawItem(item_id=f"i{k}", text=f"{texts[k % len(texts)]} #{k}") for k in range(n)]


def _prediction(rank: int, confidence: float, code: str = "99.99.01.000-00000001") -> Prediction:
    return Prediction.model_construct(
        rank=rank, ktru_code=code, name="Ноутбук", okpd2_code="99.99.01.000", confidence=confidence,
        features={}, explanation=None, why_not_chosen=None, characteristic_matches=[],
    )  # fmt: skip


def _result(*predictions: Prediction, item_id: str = "i0") -> ClassificationResult:
    return ClassificationResult.model_construct(
        item_id=item_id, extracted=None, predictions=list(predictions), path="fast", norm_key=None,
        flags=[], mode="normal", timings_ms={}, error=None,
    )  # fmt: skip


def test_stub_pipeline_passes_contract_checks() -> None:
    pipeline = load_pipeline(Settings(pipeline="stub"))
    assert isinstance(pipeline, StubPipeline)
    items = _items(400)
    checked = check_results(items, pipeline.classify_batch(items, "v1"), known_codes=FAKE_CODES)
    assert [r.item_id for r in checked] == [it.item_id for it in items]
    failed = [r for r in checked if r.error]
    assert len(failed) == 100
    assert all("__fail__" in items[int(r.item_id[1:])].text for r in failed)
    assert all(r.predictions and r.predictions[0].rank == 1 for r in checked if not r.error)


def test_missing_and_foreign_results() -> None:
    items = [RawItem(item_id="a", text="x"), RawItem(item_id="b", text="y")]
    checked = check_results(
        items, [_result(_prediction(1, 0.9), item_id="b"), _result(_prediction(1, 0.9), item_id="z")]
    )
    assert checked[0].item_id == "a"
    assert checked[0].error == "ML не вернул результат для позиции."
    assert checked[1].item_id == "b"
    assert checked[1].error is None


@pytest.mark.parametrize(
    ("predictions", "reason"),
    [
        ((), "не предложено ни одного кода"),
        ((_prediction(2, 0.5),), "ранги должны идти подряд"),
        ((_prediction(1, 0.5), _prediction(1, 0.4)), "ранги должны идти подряд"),
        ((_prediction(1, 1.2),), "вне диапазона"),
        ((_prediction(1, float("nan")),), "вне диапазона"),
        ((_prediction(1, 0.3), _prediction(2, 0.5)), "растёт с рангом"),
        ((_prediction(1, 0.6), _prediction(2, 0.5)), "сумма уверенностей больше 1"),
    ],
)
def test_invalid_answers_become_item_errors(predictions: tuple[Prediction, ...], reason: str) -> None:
    [checked] = check_results([RawItem(item_id="i0", text="x")], [_result(*predictions)])
    assert checked.error is not None
    assert reason in checked.error
    assert checked.predictions == []


def test_rounding_noise_is_clamped() -> None:
    [checked] = check_results([RawItem(item_id="i0", text="x")], [_result(_prediction(1, 1.0000001))])
    assert checked.error is None
    assert checked.predictions[0].confidence == 1.0


def test_sum_after_ml_rounding_is_accepted() -> None:
    """Настоящий ответ ml.pipeline: нормирование, затем округление до 4 знаков — сумма 1.0001."""
    predictions = (_prediction(1, 0.3719), _prediction(2, 0.3716), _prediction(3, 0.2566))
    [checked] = check_results([RawItem(item_id="i0", text="x")], [_result(*predictions)])
    assert checked.error is None
    assert [p.confidence for p in checked.predictions] == [0.3719, 0.3716, 0.2566]


def test_known_codes() -> None:
    first, unknown = "99.99.01.000-00000001", "00.00.00.000-00000000"
    [rank1_unknown] = check_results(
        [RawItem(item_id="i0", text="x")], [_result(_prediction(1, 0.9, unknown))], known_codes={first}
    )
    assert "нет в версии каталога" in rank1_unknown.error
    [alt_unknown] = check_results(
        [RawItem(item_id="i0", text="x")],
        [_result(_prediction(1, 0.5, first), _prediction(2, 0.3, unknown), _prediction(3, 0.1, first))],
        known_codes={first},
    )
    assert [(p.rank, p.ktru_code) for p in alt_unknown.predictions] == [(1, first), (2, first)]


def test_describe_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ml_adapter, "_installed", lambda module: True)
    assert describe_mode(Settings(pipeline="stub"), {"LLM_API_KEY": "k"})["llm_enabled"] is False
    real = Settings(pipeline="real")
    assert describe_mode(real, {}) == {
        "extractor": "hybrid",
        "reranker": "cross_encoder",
        "device": "cpu",
        "llm_enabled": False,
    }
    assert describe_mode(real, {"LLM_API_KEY": "k", "LLM_BASE_URL": "http://llm"})["llm_enabled"] is True
    assert describe_mode(real, {"LLM_API_KEY": "k", "RERANKER": "llm"}) == {
        "extractor": "hybrid",
        "reranker": "llm",
        "device": "cpu",
        "llm_enabled": True,
    }
    assert describe_mode(real, {"LLM_BASE_URL": "http://llm"})["llm_enabled"] is False
    assert describe_mode(real, {"EXTRACTOR": "magic", "DEVICE": "tpu"})["extractor"] == "hybrid"


def test_describe_mode_reports_reranker_fallbacks(monkeypatch: pytest.MonkeyPatch) -> None:
    """Как ml/rerank.py: llm без ключа и cross_encoder без sentence-transformers работают как none."""
    real = Settings(pipeline="real")
    monkeypatch.setattr(ml_adapter, "_installed", lambda module: False)
    assert describe_mode(real, {})["reranker"] == "none"
    monkeypatch.setattr(ml_adapter, "_installed", lambda module: True)
    assert describe_mode(real, {"RERANKER": "llm"})["reranker"] == "none"
