"""KtruPipeline — реализация Pipeline из contracts/ml_contracts.py. Единственная точка входа для бэкенда:

    from ml.pipeline import get_pipeline
    results = get_pipeline().classify_batch(items, catalog_version_id, options)

Конвейер: атрибуты (правила) → поиск кандидатов (вектор + название + обязательные характеристики)
→ полная сверка характеристик → реранкер для неуверенных → калибровка уверенности → топ-3 с пояснениями.
"""

from __future__ import annotations

import logging
import math
import threading
import time
from collections import OrderedDict
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Optional

from contracts.ml_contracts import (
    Candidate,
    ClassificationResult,
    ExtractedAttributes,
    PipelineOptions,
    Prediction,
    RawItem,
    SearchQuery,
    normalize_text,
)

from . import calibrate as calibration
from .catalog import Catalog, entry_from_row, load_jsonl
from .charmatch import CharMatcher, EntryMatch, build_query_context
from .config import Settings
from .explain import explanation, why_not_chosen
from .extract import RulesExtractor
from .rerank import NoneReranker, make_reranker
from .retrieve import MemoryRetriever

log = logging.getLogger(__name__)

W_RETRIEVAL = 0.45
W_CHARS = 0.55
KEEP = 5  # сколько кандидатов держим после сверки (для признаков и калибровки)


@dataclass
class Scored:
    candidate: Candidate
    match: EntryMatch
    final: float
    rerank: float = 0.0
    features: dict[str, float] = field(default_factory=dict)

    @property
    def ktru_code(self) -> str:
        return self.candidate.ktru_code


@dataclass
class Ranked:
    item_id: str
    text: str
    extracted: Optional[ExtractedAttributes] = None
    candidates: list[Scored] = field(default_factory=list)
    path: Optional[str] = None
    error: Optional[str] = None
    timings_ms: dict[str, float] = field(default_factory=dict)


class KtruPipeline:
    def __init__(self, settings: Optional[Settings] = None, catalog: Optional[Catalog] = None,
                 calibrator=None, reranker=None, extractor=None):
        t0 = time.perf_counter()
        self.settings = settings or Settings()
        self.catalog = catalog or Catalog(load_jsonl(self.settings.resolved_catalog_path()))
        self.matcher = CharMatcher(self.catalog)
        embedder = None
        if self.settings.embedder_model:
            from .embed import SentenceTransformerEmbedder  # noqa: PLC0415

            embedder = SentenceTransformerEmbedder(self.settings.embedder_model, self.settings.device)
        self.retriever = MemoryRetriever(self.catalog, self.matcher, embedder, cache_dir=self.settings.embed_cache_dir)
        self.extractor = extractor or RulesExtractor()
        if reranker is not None:
            self.reranker, rerank_degraded = reranker, False
        else:
            self.reranker, rerank_degraded = make_reranker(self.settings)
        self.calibrator = calibrator or calibration.load(self.settings.calibrator_path)
        # LLM была нужна (hybrid/llm-извлечение или llm-реранкер), но её нет → режим degraded.
        self.llm_wanted_but_missing = rerank_degraded or (
            self.settings.extractor in ("llm", "hybrid") and not self.settings.llm_enabled
        )
        self.load_ms = (time.perf_counter() - t0) * 1000

    # --- контракт -----------------------------------------------------------------------------------

    def is_ready(self) -> dict[str, bool]:
        return {"models": True, "catalog": len(self.retriever.items) > 0}

    def add_examples(self, pairs: Iterable[tuple[str, str]] | dict[str, str]) -> int:
        """Подтверждённые пользователями пары «текст → код» (FR-4.7). Повтор текста заменяет код. → число пар."""
        pairs = dict(pairs.items() if isinstance(pairs, dict) else pairs)
        self.retriever.add_examples(pairs)
        return len(pairs)

    def classify_batch(self, items: list[RawItem], catalog_version_id: str = "",
                       options: Optional[PipelineOptions] = None) -> list[ClassificationResult]:
        """Классификация по каталогу этого экземпляра. Версии каталога различает CatalogVersions ниже."""
        opts = options or PipelineOptions()
        ranked = self.rank(items, use_feedback=opts.use_feedback, options=opts)
        mode = "degraded" if (opts.use_llm and self.llm_wanted_but_missing) else "normal"
        return [self._result(r, mode) for r in ranked]

    # --- ранжирование (его же использует обучение калибратора) ----------------------------------------

    def rank(self, items: list[RawItem], use_feedback: bool = True,
             options: Optional[PipelineOptions] = None) -> list[Ranked]:
        opts = options or PipelineOptions()
        # Одинаковые тексты внутри вызова считаем один раз.
        uniq: dict[str, RawItem] = {}
        key_of = []
        for it in items:
            key = normalize_text(it.text)
            key_of.append(key)
            uniq.setdefault(key, it)
        keys = list(uniq)
        todo = [uniq[k] for k in keys]
        out: dict[str, Ranked] = {}
        bad = [k for k, it in zip(keys, todo) if not it.text.strip()]
        for k in bad:
            out[k] = Ranked(uniq[k].item_id, uniq[k].text, error="Пустой текст позиции")
        good = [(k, it) for k, it in zip(keys, todo) if k not in out]
        if good:
            try:
                for k, r in zip([k for k, _ in good], self._rank_good([it for _, it in good], use_feedback, opts)):
                    out[k] = r
            except Exception:  # пакетная ошибка — пробуем по одной, чтобы упала только плохая позиция
                log.exception("Ошибка пакета, обрабатываю позиции по одной")
                for k, it in good:
                    try:
                        out[k] = self._rank_good([it], use_feedback, opts)[0]
                    except Exception as e:  # noqa: BLE001
                        out[k] = Ranked(it.item_id, it.text, error=f"Не удалось обработать позицию: {e}")
        result = []
        for it, k in zip(items, key_of):
            r = out[k]
            result.append(Ranked(it.item_id, it.text, r.extracted, r.candidates, r.path, r.error, r.timings_ms))
        return result

    def _rank_good(self, items: list[RawItem], use_feedback: bool, opts: PipelineOptions) -> list[Ranked]:
        n = len(items)
        t = time.perf_counter()
        extracted = self.extractor.extract_batch(items)
        t_extract = time.perf_counter() - t

        t = time.perf_counter()
        queries = [SearchQuery(text=it.text, extracted=ex, catalog_version_id="", top_k=opts.top_k)
                   for it, ex in zip(items, extracted)]
        cands = self.retriever.search_batch(queries, use_feedback=use_feedback)
        t_retrieve = time.perf_counter() - t

        t = time.perf_counter()
        ranked = []
        for it, ex, cs in zip(items, extracted, cands):
            q = build_query_context(it.text)
            scored = [Scored(c, self.matcher.match_entry(self.retriever.entry(c.ktru_code), q), 0.0) for c in cs]
            for s in scored:
                s.final = W_RETRIEVAL * s.candidate.fused_score + W_CHARS * s.match.score
            scored.sort(key=lambda s: -s.final)
            ranked.append(Ranked(it.item_id, it.text, ex, scored[:KEEP]))
        t_match = time.perf_counter() - t

        t = time.perf_counter()
        need = [r for r in ranked if r.candidates and self._needs_rerank(r, opts)]
        if need and not isinstance(self.reranker, NoneReranker):
            results = self.reranker.rerank_batch([(r.extracted, [s.candidate for s in r.candidates]) for r in need],
                                                 texts=[r.text for r in need])
            for r, rr in zip(need, results):
                self._apply_rerank(r, rr)
                r.path = "rerank"
        for r in ranked:
            if r.candidates and r.path is None:
                r.path = "fast"
        t_rerank = time.perf_counter() - t

        for r in ranked:
            self._features(r, use_feedback)
        per = {k: v * 1000 / n for k, v in
               (("extract", t_extract), ("retrieve", t_retrieve), ("match", t_match), ("rerank", t_rerank))}
        per["total"] = sum(per.values())
        for r in ranked:
            r.timings_ms = dict(per)
        return ranked

    def _needs_rerank(self, r: Ranked, opts: PipelineOptions) -> bool:
        top = r.candidates[0].final
        second = r.candidates[1].final if len(r.candidates) > 1 else 0.0
        return not (top >= opts.fast_path_min_score and top - second >= opts.fast_path_min_margin)

    def _apply_rerank(self, r: Ranked, rr) -> None:
        score = dict(zip(rr.ordered_codes, rr.scores))
        if not score:
            return
        lo, hi = min(score.values()), max(score.values())
        for s in r.candidates:
            raw = score.get(s.ktru_code)
            s.rerank = 0.0 if raw is None or hi == lo else (raw - lo) / (hi - lo)
            w = self.settings.rerank_weight
            s.final = (1 - w) * s.final + w * s.rerank
        r.candidates.sort(key=lambda s: -s.final)

    def _features(self, r: Ranked, use_feedback: bool) -> None:
        cs = r.candidates
        if not cs:
            return
        best_vec = max(cs, key=lambda s: s.candidate.vector_score).ktru_code
        best_name = max(cs, key=lambda s: s.candidate.text_score).ktru_code
        agree = 1.0 if best_vec == best_name else 0.0
        ext_conf = (r.extracted.field_confidence.get("name", 0.5) if r.extracted else 0.0)
        for i, s in enumerate(cs):
            others = [o.final for j, o in enumerate(cs) if j != i]
            s.features = {
                "top1_fused": s.final,
                "margin_top1_top2": s.final - (max(others) if others else 0.0),
                "vector_score": s.candidate.vector_score,
                "text_score": s.candidate.text_score,
                "char_match_ratio": s.match.ratio,
                "rerank_score": s.rerank,
                "extraction_confidence": ext_conf,
                "vec_text_agreement": agree if s.ktru_code == best_vec else 0.0,
                "confirmed_vote": self.retriever.example_vote(r.text, s.ktru_code) if use_feedback else 0.0,
            }

    # --- сборка ответа по контракту --------------------------------------------------------------------

    def _result(self, r: Ranked, mode: str) -> ClassificationResult:
        if r.error:
            return ClassificationResult(item_id=r.item_id, error=r.error, mode=mode, timings_ms=r.timings_ms)
        top = r.candidates[:3]
        conf = [self.calibrator.predict(s.features) for s in top]
        order = sorted(range(len(top)), key=lambda i: -conf[i])  # уверенность не растёт с рангом
        top, conf = [top[i] for i in order], [conf[i] for i in order]
        total = sum(conf)
        if total > 1.0:
            conf = [c / total for c in conf]
        preds = []
        for rank, (s, c) in enumerate(zip(top, conf), start=1):
            preds.append(Prediction(
                rank=rank,
                ktru_code=s.ktru_code,
                name=s.candidate.name,
                okpd2_code=s.candidate.okpd2_code,
                # Вниз, а не к ближайшему: после округления сумма по позиции должна остаться ≤ 1.
                confidence=math.floor(min(max(c, 0.0), 1.0) * 10_000) / 10_000,
                features={k: round(v, 4) for k, v in s.features.items()},
                explanation=explanation(s.match) if rank == 1 else None,
                why_not_chosen=None if rank == 1 else why_not_chosen(
                    top[0].match, s.match, top[0].candidate.text_score, s.candidate.text_score),
                characteristic_matches=s.match.matches,
            ))
        ex = r.extracted
        flags = []
        if top and top[0].match.required_missing:
            flags.append("required_missing")
        if ex and ex.field_confidence.get("name", 1.0) < 0.5:
            flags.append("low_extraction_confidence")
        norm_key = None
        if ex and ex.brand and ex.model:
            norm_key = f"{ex.name.lower()}|{ex.brand.lower()}|{ex.model.lower()}"
        return ClassificationResult(
            item_id=r.item_id, extracted=ex, predictions=preds, path=r.path, norm_key=norm_key,
            flags=flags, mode=mode, timings_ms={k: round(v, 2) for k, v in r.timings_ms.items()},
        )


class CatalogVersions:
    """Pipeline поверх нескольких версий каталога (FR-8.1: новая версия без перезапуска воркера).

    Каждая задача обрабатывается той версией каталога, которая в ней закреплена (catalog_version_id),
    поэтому коды ML всегда проходят проверку бэкенда по каталогу задачи. Новую версию бэкенд передаёт строками
    из catalog_items: load_version(version_id, rows). Индекс строится, пока задачи обслуживает старая версия,
    затем подменяется. В памяти держится не больше KEEP версий: текущая и предыдущая — для задач, которые
    стартовали до импорта. Модели, калибратор и реранкер общие для всех версий.
    """

    KEEP = 2

    def __init__(self, settings: Optional[Settings] = None, calibrator=None, reranker=None, extractor=None):
        self.settings = settings or Settings()
        self._calibrator = calibrator or calibration.load(self.settings.calibrator_path)
        self._reranker = reranker
        self._extractor = extractor or RulesExtractor()
        self._versions: OrderedDict[str, KtruPipeline] = OrderedDict()
        self._examples: dict[str, str] = {}
        self._lock = threading.Lock()

    def load_version(self, version_id: str, rows: Iterable[dict]) -> int:
        """Строит индекс версии каталога из строк CatalogRow (dict). Долго (~50 с на полном КТРУ) — вызывать
        из отдельного потока; классификация других версий в это время продолжается. → число позиций."""
        return self._add(version_id, Catalog(entry_from_row(r) for r in rows))

    def load_file(self, version_id: str, path: str) -> int:
        return self._add(version_id, Catalog(load_jsonl(path)))

    def _add(self, version_id: str, catalog: Catalog) -> int:
        engine = KtruPipeline(self.settings, catalog=catalog, calibrator=self._calibrator,
                              reranker=self._reranker, extractor=self._extractor)
        self._reranker = self._reranker or engine.reranker
        with self._lock:
            engine.add_examples(self._examples)
            self._versions[version_id] = engine
            self._versions.move_to_end(version_id)
            while len(self._versions) > self.KEEP:
                old, _ = self._versions.popitem(last=False)
                log.info("Версия каталога %s выгружена из памяти", old)
        return len(catalog)

    def loaded_versions(self) -> list[str]:
        """Загруженные версии, последняя — текущая. Бэкенд закрепляет за новой задачей последнюю."""
        with self._lock:
            return list(self._versions)

    def add_examples(self, pairs: Iterable[tuple[str, str]] | dict[str, str]) -> int:
        pairs = dict(pairs.items() if isinstance(pairs, dict) else pairs)
        with self._lock:
            self._examples.update(pairs)
            engines = list(self._versions.values())
        for engine in engines:
            engine.add_examples(pairs)
        return len(pairs)

    def is_ready(self) -> dict[str, bool]:
        with self._lock:
            return {"models": True, "catalog": any(e.is_ready()["catalog"] for e in self._versions.values())}

    def classify_batch(self, items: list[RawItem], catalog_version_id: str = "",
                       options: Optional[PipelineOptions] = None) -> list[ClassificationResult]:
        with self._lock:
            engine = self._versions.get(catalog_version_id)
            if engine is None and not catalog_version_id and self._versions:
                engine = next(reversed(self._versions.values()))  # без версии — текущая (оценка, отладка)
            if engine is None and "" in self._versions:
                # Каталог из файла без указанной версии (get_pipeline() по умолчанию) обслуживает задачи любой
                # версии — так работает режим, в котором migrate и worker-ml читают один и тот же файл.
                engine = self._versions[""]
        if engine is None:
            error = f"Версия каталога {catalog_version_id or '(не указана)'} не загружена в ML-воркер."
            return [ClassificationResult(item_id=it.item_id, error=error) for it in items]
        return engine.classify_batch(items, catalog_version_id, options)


_PIPELINE: Optional[CatalogVersions] = None


def get_pipeline(load_file: bool = True) -> CatalogVersions:
    """Один экземпляр на процесс воркера.

    load_file=True — сразу загрузить каталог из KTRU_CATALOG_PATH под версией KTRU_CATALOG_VERSION_ID
    (или «» — тогда он обслуживает задачи без версии). Бэкенд, который берёт каталог из базы, вызывает
    get_pipeline(load_file=False), затем load_version(version_id, rows) для версии из catalog_versions.
    """
    global _PIPELINE
    if _PIPELINE is None:
        _PIPELINE = CatalogVersions()
        if load_file:
            settings = _PIPELINE.settings
            _PIPELINE.load_file(settings.catalog_version_id, str(settings.resolved_catalog_path()))
    return _PIPELINE
