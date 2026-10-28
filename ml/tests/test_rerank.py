"""ChatChooser: выбор кода языковой моделью среди наших кандидатов (сеть подменена)."""

import json

import httpx

from contracts.ml_contracts import Candidate
from ml.rerank import ChatChooser


def _cand(code, name):
    return Candidate(ktru_code=code, name=name, okpd2_code=code.split("-")[0], vector_score=0, text_score=0,
                     fused_score=0, characteristics=[])


CANDS = [_cand("77.77.01.000-1", "Протез радужной оболочки глаза"), _cand("77.77.02.000-1", "Рюкзак")]


def _chooser(tmp_path, reply, calls):
    def handler(request):
        calls.append(json.loads(request.content))
        if reply is None:
            return httpx.Response(500)
        return httpx.Response(200, json={"choices": [{"message": {"content": reply}}]})

    ch = ChatChooser("key", "some/model", cache_path=tmp_path / "cache.jsonl")
    ch.client = httpx.Client(transport=httpx.MockTransport(handler))
    return ch


def test_choice_is_moved_to_top_and_cached(tmp_path):
    calls = []
    ch = _chooser(tmp_path, 'Ответ: {"choice": 2}', calls)
    r = ch._one("Рюкзак школьный сине-голубой", CANDS)
    assert r.ordered_codes[0] == "77.77.02.000-1" and r.scores == [1.0, 0.0]
    assert "Рюкзак школьный" in calls[0]["messages"][1]["content"]
    ch._one("Рюкзак школьный сине-голубой", CANDS)
    assert len(calls) == 1  # второй раз — из кэша, без запроса


def test_none_fits_and_errors_keep_order(tmp_path):
    r = _chooser(tmp_path, '{"choice": 0}', [])._one("Что-то другое", CANDS)
    assert r.scores == [0.0, 0.0]
    r = _chooser(tmp_path / "e", None, [])._one("Сеть упала", CANDS)
    assert r.ordered_codes == [c.ktru_code for c in CANDS] and r.scores == [0.0, 0.0]
