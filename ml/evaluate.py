"""Оценка качества на размеченном наборе (docs/analytics/09-data-quality.md).

    python -m ml.evaluate --data data/ktru/eval/dev_synth.jsonl --split dev

Подтверждённые примеры всегда выключены (use_feedback=False), иначе метрики завышаются.
Набор test запускать не чаще раза в неделю и ничего под него не подбирать.
"""

from __future__ import annotations

import argparse
import json
import time
from collections import defaultdict
from pathlib import Path

from contracts.ml_contracts import Pipeline, PipelineOptions, RawItem

from .config import ARTIFACTS, Settings
from .pipeline import KtruPipeline

ATTR_FIELDS = ("name", "brand", "model")


def _norm_attr(v) -> str:
    return " ".join(str(v or "").lower().replace("ё", "е").split())


def ece(conf: list[float], correct: list[bool], bins: int = 10) -> float:
    total, err = len(conf), 0.0
    for b in range(bins):
        lo, hi = b / bins, (b + 1) / bins
        idx = [i for i, c in enumerate(conf) if lo <= c < hi or (b == bins - 1 and c == 1.0)]
        if idx:
            acc = sum(correct[i] for i in idx) / len(idx)
            avg = sum(conf[i] for i in idx) / len(idx)
            err += len(idx) / total * abs(acc - avg)
    return err


def _okpd_level(code: str, k: int) -> str:
    return ".".join(code.split("-")[0].split(".")[:k])


def evaluate(samples: list[dict], pipe: Pipeline, text_field: str = "raw_text", catalog_version_id: str = "") -> dict:
    """Метрики раздела 9 на размеченных строках. pipe — любой Pipeline: KtruPipeline или то, что отдаёт
    get_pipeline() в воркере (CatalogVersions) — нужен только classify_batch. Подтверждённые примеры выключены."""
    items = [RawItem(item_id=s["id"], text=s.get(text_field) or s["raw_text"]) for s in samples]
    t0 = time.perf_counter()
    results = pipe.classify_batch(items, catalog_version_id, PipelineOptions(use_feedback=False))
    seconds = time.perf_counter() - t0

    top1 = top3 = okpd = fast = any_valid = okpd_top3 = 0
    level = [0, 0]  # ОКПД2 до класса XX.XX и группы XX.XX.XX
    okpd_conf, okpd_correct = [], []
    conf, correct, errors = [], [], []
    attr_ok = attr_n = 0
    attr_field = defaultdict(lambda: [0, 0])
    by_group = defaultdict(lambda: [0, 0])
    for s, r in zip(samples, results):
        gold = s["expected_ktru_code"]
        codes = [p.ktru_code for p in r.predictions]
        hit1 = bool(codes) and codes[0] == gold
        top1 += hit1
        valid = {gold, *s.get("alt_valid_codes", [])}
        any_valid += bool(codes) and codes[0] in valid
        top3 += gold in codes[:3]
        okpd += bool(codes) and codes[0].split("-")[0] == gold.split("-")[0]
        okpd_top3 += gold.split("-")[0] in [c.split("-")[0] for c in codes[:3]]
        for i, k in enumerate((2, 3)):
            level[i] += bool(codes) and _okpd_level(codes[0], k) == _okpd_level(gold, k)
        fast += r.path == "fast"
        group = gold.split("-")[0]
        by_group[group][0] += hit1
        by_group[group][1] += 1
        if r.predictions:
            conf.append(r.predictions[0].confidence)
            correct.append(hit1)
            okpd_conf.append(r.predictions[0].confidence)
            okpd_correct.append(codes[0].split("-")[0] == gold.split("-")[0])
        if not hit1:
            errors.append({"id": s["id"], "text": s["raw_text"], "gold": gold, "pred": codes[:3],
                           "conf": r.predictions[0].confidence if r.predictions else None, "error": r.error})
        exp = s.get("expected_attrs")
        if exp and r.extracted:
            for f in ATTR_FIELDS:
                ok = _norm_attr(exp.get(f)) == _norm_attr(getattr(r.extracted, f))
                attr_ok += ok
                attr_n += 1
                attr_field[f][0] += ok
                attr_field[f][1] += 1
    n = len(samples)
    groups = {g: round(a / b, 4) for g, (a, b) in sorted(by_group.items())}
    return {
        "samples": n,
        "top1_full_code": round(top1 / n, 4),
        "top1_any_valid": round(any_valid / n, 4),
        "top1_okpd2": round(okpd / n, 4),
        "top1_okpd2_class": round(level[0] / n, 4),
        "top1_okpd2_group": round(level[1] / n, 4),
        "okpd2_top3": round(okpd_top3 / n, 4),
        "top3": round(top3 / n, 4),
        "top1_macro_by_okpd2": round(sum(groups.values()) / len(groups), 4) if groups else None,
        "attributes_accuracy": round(attr_ok / attr_n, 4) if attr_n else None,
        "attributes_by_field": {f: round(a / b, 4) for f, (a, b) in attr_field.items()},
        "ece": round(ece(conf, correct), 4) if conf else None,
        # Для наборов из СТЕ (эталон «<ОКПД2>-X») точный код не проверяется — уверенность сверяем с ОКПД2.
        "ece_okpd2": round(ece(okpd_conf, okpd_correct), 4) if okpd_conf else None,
        "fast_path_share": round(fast / n, 4),
        "seconds_per_1000": round(seconds / n * 1000, 1),
        "by_okpd2": groups,
        "errors": errors,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Метрики ML на размеченном наборе")
    ap.add_argument("--data", required=True)
    ap.add_argument("--split", default="dev", choices=["dev", "test"])
    ap.add_argument("--tier", default=None, help="core / extra — только позиции этого tier (разметка v3)")
    ap.add_argument("--out", default=None)
    ap.add_argument("--text-field", default="raw_text", help="raw_text_spec — строки с характеристиками (наборы СТЕ)")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()
    samples = [json.loads(line) for line in open(args.data, encoding="utf-8") if line.strip()]
    samples = [s for s in samples if s.get("split") == args.split and (not args.tier or s.get("tier") == args.tier)]
    if not samples:
        raise SystemExit(f"В {args.data} нет позиций split={args.split}")
    if args.limit:
        samples = samples[: args.limit]
    pipe = KtruPipeline(Settings())
    m = evaluate(samples, pipe, args.text_field)
    out = Path(args.out or ARTIFACTS / f"eval_{args.split}.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(m, ensure_ascii=False, indent=2), encoding="utf-8")
    cal = "обученный" if getattr(pipe.calibrator, "fitted", False) else "эвристика"
    cat = "есть" if pipe.category is not None else "нет"
    print(f"split={args.split}  tier={args.tier or 'все'}  позиций={m['samples']}  поле={args.text_field}  "
          f"реранкер={pipe.reranker.name}  калибратор={cal}  модель категорий={cat}")
    for k in ("top1_full_code", "top1_any_valid", "top1_okpd2", "top1_okpd2_class", "top1_okpd2_group", "okpd2_top3", "top3",
              "top1_macro_by_okpd2", "attributes_accuracy", "ece", "ece_okpd2", "fast_path_share", "seconds_per_1000"):
        print(f"  {k:22} {m[k]}")
    print(f"  по полям атрибутов     {m['attributes_by_field']}")
    print(f"ошибок: {len(m['errors'])}, подробности — {out}")


if __name__ == "__main__":
    main()
