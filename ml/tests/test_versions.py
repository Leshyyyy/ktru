"""CatalogVersions: несколько версий каталога в одном воркере и подтверждённые примеры от бэкенда."""

from contracts.ml_contracts import Pipeline, PipelineOptions, RawItem

from ml.calibrate import HeuristicCalibrator
from ml.pipeline import CatalogVersions
from ml.rerank import NoneReranker


def _row(code, name, chars=()):
    return {"ktru_code": code, "okpd2_code": code.split("-")[0], "name": name, "status": "active",
            "characteristics": list(chars)}


V1 = [_row("77.77.01.000-00000001", "Ноутбук"), _row("77.77.02.000-00000001", "Монитор")]
V2 = [_row("77.77.01.000-00000002", "Ноутбук"), _row("77.77.02.000-00000001", "Монитор")]


def _mgr():
    m = CatalogVersions(calibrator=HeuristicCalibrator(), reranker=NoneReranker(), category=None)
    m.load_version("v1", V1)
    return m


def _top(m, version, text="Ноутбук"):
    (r,) = m.classify_batch([RawItem(item_id="1", text=text)], version, PipelineOptions(use_feedback=True))
    return r


def test_contract_and_routing_by_job_version():
    m = _mgr()
    assert isinstance(m, Pipeline) and m.is_ready() == {"models": True, "catalog": True}
    m.load_version("v2", V2)
    assert _top(m, "v1").predictions[0].ktru_code == "77.77.01.000-00000001"  # задача на старой версии
    assert _top(m, "v2").predictions[0].ktru_code == "77.77.01.000-00000002"  # новая задача
    assert m.loaded_versions() == ["v1", "v2"]


def test_unknown_version_is_item_error_not_exception():
    r = _top(_mgr(), "v-unknown")
    assert r.error and "не загружена" in r.error and not r.predictions


def test_only_two_versions_kept():
    m = _mgr()
    m.load_version("v2", V2)
    m.load_version("v3", V1)
    assert m.loaded_versions() == ["v2", "v3"]
    assert _top(m, "v1").error


def test_examples_reach_current_and_future_versions():
    m = _mgr()
    assert m.add_examples([("Ноутбук для бухгалтерии", "77.77.01.000-00000001")]) == 1
    assert _top(m, "v1", "ноутбук  для бухгалтерии").predictions[0].features["confirmed_vote"] == 1.0
    m.load_version("v2", V1)  # версия, загруженная позже, тоже знает пример
    assert _top(m, "v2", "Ноутбук для бухгалтерии").predictions[0].features["confirmed_vote"] == 1.0


def test_file_catalog_without_version_serves_any_job_version():
    m = CatalogVersions(calibrator=HeuristicCalibrator(), reranker=NoneReranker(), category=None)
    m.load_version("", V1)  # как get_pipeline() с KTRU_CATALOG_PATH и без KTRU_CATALOG_VERSION_ID
    assert _top(m, "3f1c0e2a-uuid-from-db").predictions[0].ktru_code == "77.77.01.000-00000001"


def test_describe_mode_and_empty_env(monkeypatch):
    from ml.config import Settings

    # docker-compose передаёт объявленную без значения переменную пустой строкой — это «не задано».
    for name in ("EMBEDDING_DIM", "CALIBRATOR_PATH", "RERANKER", "EXTRACTOR"):
        monkeypatch.setenv(name, "")
    s = Settings()
    assert s.embedding_dim == 1024 and s.calibrator_path.endswith("calibrator.joblib") and s.reranker == "none"
    m = CatalogVersions(s, calibrator=HeuristicCalibrator(), reranker=NoneReranker(), category=None)
    assert m.describe_mode() == {"extractor": "rules", "reranker": "none", "device": "cpu", "llm_enabled": False}
    monkeypatch.setenv("RERANKER", "llm")
    monkeypatch.setenv("LLM_API_KEY", "")
    assert CatalogVersions(Settings(), category=None).describe_mode()["reranker"] == "none"  # без ключа LLM нет
