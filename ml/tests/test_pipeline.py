"""Инварианты контракта Pipeline (contracts/ml_contracts.py) и выбор внутри одноимённой группы."""

from contracts.ml_contracts import ClassificationResult, Pipeline, PipelineOptions, RawItem


def _run(pipe, *texts, **opts):
    items = [RawItem(item_id=str(i), text=t) for i, t in enumerate(texts)]
    return pipe.classify_batch(items, "v1", PipelineOptions(**opts))


def test_implements_protocol(fixture_pipeline):
    assert isinstance(fixture_pipeline, Pipeline)
    assert fixture_pipeline.is_ready() == {"models": True, "catalog": True}


def test_contract_invariants(fixture_pipeline):
    texts = ['Ноутбук Lenovo IdeaPad 3 15IAU7, 15.6", 16 ГБ', "Бумага офисная А4, 80 г/м2", "", "Шприц 5 мл"]
    res = _run(fixture_pipeline, *texts)
    assert [r.item_id for r in res] == ["0", "1", "2", "3"]  # порядок и длина сохраняются
    for r in res:
        ClassificationResult.model_validate(r.model_dump())
        if r.error:
            continue
        conf = [p.confidence for p in r.predictions]
        assert 1 <= len(conf) <= 3
        assert conf == sorted(conf, reverse=True)  # уверенность не растёт с рангом
        assert sum(conf) <= 1.0 + 1e-6
        assert [p.rank for p in r.predictions] == list(range(1, len(conf) + 1))
        assert r.path in ("fast", "rerank") and "total" in r.timings_ms
    assert res[2].error and not res[2].predictions  # пустая позиция не роняет пакет


def test_duplicates_get_same_answer(fixture_pipeline):
    a, b = _run(fixture_pipeline, "Принтер лазерный", "  принтер   лазерный ")
    assert a.predictions[0].ktru_code == b.predictions[0].ktru_code


def test_tighter_range_wins_inside_same_name(synth_pipeline):
    (r,) = _run(synth_pipeline, 'Ноутбук, диагональ экрана 17.3", SSD')
    assert r.predictions[0].ktru_code == "77.77.01.000-00000017"
    (r,) = _run(synth_pipeline, "Ноутбук, диагональ экрана 16 дюймов")
    assert r.predictions[0].ktru_code == "77.77.01.000-00000016"


def test_exclusive_bounds_and_units(synth_pipeline):
    (r,) = _run(synth_pipeline, "Шприц одноразовый 3 мл")
    assert r.predictions[0].ktru_code == "77.77.02.000-00000003"
    (r,) = _run(synth_pipeline, "Шприц одноразовый объем 5 мл")
    assert r.predictions[0].ktru_code == "77.77.02.000-00000005"


def test_cyrillic_and_latin_format(synth_pipeline):
    (r,) = _run(synth_pipeline, "Бумага для офисной техники, формат A3 (латиница)")
    assert r.predictions[0].ktru_code == "77.77.03.000-00000003"


def test_yes_no_characteristic_not_confused_with_product_name(synth_pipeline):
    (r,) = _run(synth_pipeline, "Игла спинальная, проводниковая игла: нет")
    assert r.predictions[0].ktru_code == "77.77.04.000-00000002"
    (r,) = _run(synth_pipeline, "Игла спинальная с проводниковой иглой")
    assert r.predictions[0].ktru_code == "77.77.04.000-00000001"


def test_archived_and_enlarged_never_returned(synth_pipeline):
    (r,) = _run(synth_pipeline, "Монитор 24 дюйма")
    codes = {p.ktru_code for p in r.predictions}
    assert "77.77.05.000-00000001" not in codes and "77.77.05.000-00000099" not in codes


def test_feedback_cache(synth_pipeline):
    text = "Ноутбук для бухгалтерии"
    synth_pipeline.retriever.add_examples({text: "77.77.01.000-00000016"})
    (with_fb,) = _run(synth_pipeline, text, use_feedback=True)
    assert with_fb.predictions[0].features["confirmed_vote"] == 1.0
    (without,) = _run(synth_pipeline, text, use_feedback=False)
    assert all(p.features["confirmed_vote"] == 0.0 for p in without.predictions)


def test_required_missing_flag_and_explanation(synth_pipeline):
    (r,) = _run(synth_pipeline, "Ноутбук")
    assert "required_missing" in r.flags
    assert r.predictions[0].explanation
    for p in r.predictions[1:]:
        assert p.why_not_chosen


def test_query_interval_picks_matching_range(synth_pipeline):
    (r,) = _run(synth_pipeline, "Шприц одноразовый, объем от 3 до 5 мл")
    assert r.predictions[0].ktru_code == "77.77.02.000-00000005"


def test_missing_required_does_not_push_to_other_group(synth_pipeline):
    # У «Иглы спинальной» обязательная характеристика не указана — это не повод уходить в другую группу.
    (r,) = _run(synth_pipeline, "Игла спинальная")
    assert r.predictions[0].okpd2_code == "77.77.04.000"


def test_rounded_confidences_sum_at_most_one(fixture_pipeline):
    for text in ("Бумага офисная А4 80 г/м2", "Принтер", "Ноутбук", "Шприц одноразовый 5 мл трёхкомпонентный"):
        (r,) = _run(fixture_pipeline, text)
        assert sum(p.confidence for p in r.predictions) <= 1.0


def test_no_key_default_mode_is_normal(fixture_pipeline):
    (r,) = _run(fixture_pipeline, "Принтер лазерный")
    assert r.mode == "normal"


class _FakeEmbedder:
    """Детерминированные «эмбеддинги» по буквам — проверяем путь с векторами без скачивания модели."""

    model_name = "fake/letters"
    dim = 64
    calls = 0

    def embed(self, texts, kind="query"):
        import numpy as np

        _FakeEmbedder.calls += 1
        out = []
        for t in texts:
            v = np.zeros(64, dtype=np.float32)
            for ch in t.lower():
                v[ord(ch) % 64] += 1.0
            out.append((v / (np.linalg.norm(v) or 1.0)).tolist())
        return out


def test_dense_retrieval_and_embedding_cache(tmp_path):
    from ml.catalog import Catalog, entry_from_row
    from ml.charmatch import CharMatcher
    from ml.retrieve import MemoryRetriever

    from conftest import SYNTH_ROWS

    catalog = Catalog([entry_from_row(r) for r in SYNTH_ROWS])
    first = MemoryRetriever(catalog, CharMatcher(catalog), _FakeEmbedder(), cache_dir=tmp_path)
    assert first.dense is not None and list(tmp_path.glob("catalog_fake__letters_*.npy"))
    calls = _FakeEmbedder.calls
    second = MemoryRetriever(catalog, CharMatcher(catalog), _FakeEmbedder(), cache_dir=tmp_path)
    assert _FakeEmbedder.calls == calls  # каталог взят из кэша, не пересчитан
    assert (second.dense == first.dense).all()


def test_vectors_from_store_are_used_instead_of_embedding_catalog():
    import numpy as np
    from ml.catalog import Catalog, entry_from_row
    from ml.charmatch import CharMatcher
    from ml.retrieve import MemoryRetriever
    from ml.vector_store import pad

    from conftest import SYNTH_ROWS

    catalog = Catalog([entry_from_row(r) for r in SYNTH_ROWS])
    emb = _FakeEmbedder()
    own = MemoryRetriever(catalog, CharMatcher(catalog), emb)
    calls = _FakeEmbedder.calls
    # Векторы позиций, как их отдаёт pgvector: дополнены нулями до vector(1024), косинус тот же.
    stored = {e.ktru_code: pad(own.dense[own.name_idx[j]][None, :], 1024)[0] for j, e in enumerate(own.items)}
    from_db = MemoryRetriever(catalog, CharMatcher(catalog), emb, vectors=stored)
    assert _FakeEmbedder.calls == calls  # каталог не пересчитывался
    assert np.allclose(from_db.dense, own.dense)
