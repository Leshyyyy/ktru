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

from contracts.ml_contracts import PipelineOptions, RawItem

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


def evaluate(samples: list[dict], pipe: KtruPipeline) -> dict:
    items = [RawItem(item_id=s["id"], text=s["raw_text"]) for s in samples]
    t0 = time.perf_counter()
    results = pipe.classify_batch(items, "", PipelineOptions(use_feedback=False))
    seconds = time.perf_counter() - t0

    top1 = top3 = okpd = fast = 0
    conf, correct, errors = [], [], []
    attr_ok = attr_n = 0
    attr_field = defaultdict(lambda: [0, 0])
    by_group = defaultdict(lambda: [0, 0])
    for s, r in zip(samples, results):
        gold = s["expected_ktru_code"]
        codes = [p.ktru_code for p in r.predictions]
        hit1 = bool(codes) and codes[0] == gold
        top1 += hit1
        top3 += gold in codes[:3]
        okpd += bool(codes) and codes[0].split("-")[0] == gold.split("-")[0]
        fast += r.path == "fast"
        group = gold.split("-")[0]
        by_group[group][0] += hit1
        by_group[group][1] += 1
        if r.predictions:
            conf.append(r.predictions[0].confidence)
            correct.append(hit1)
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
        "top1_okpd2": round(okpd / n, 4),
        "top3": round(top3 / n, 4),
        "top1_macro_by_okpd2": round(sum(groups.values()) / len(groups), 4) if groups else None,
        "attributes_accuracy": round(attr_ok / attr_n, 4) if attr_n else None,
        "attributes_by_field": {f: round(a / b, 4) for f, (a, b) in attr_field.items()},
        "ece": round(ece(conf, correct), 4) if conf else None,
        "fast_path_share": round(fast / n, 4),
        "seconds_per_1000": round(seconds / n * 1000, 1),
        "by_okpd2": groups,
        "errors": errors,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Метрики ML на размеченном наборе")
    ap.add_argument("--data", required=True)
    ap.add_argument("--split", default="dev", choices=["dev", "test"])
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    samples = [json.loads(line) for line in open(args.data, encoding="utf-8") if line.strip()]
    samples = [s for s in samples if s.get("split") == args.split]
    if not samples:
        raise SystemExit(f"В {args.data} нет позиций split={args.split}")
    pipe = KtruPipeline(Settings())
    m = evaluate(samples, pipe)
    out = Path(args.out or ARTIFACTS / f"eval_{args.split}.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(m, ensure_ascii=False, indent=2), encoding="utf-8")
    cal = "обученный" if getattr(pipe.calibrator, "fitted", False) else "эвристика"
    print(f"split={args.split}  позиций={m['samples']}  реранкер={pipe.reranker.name}  калибратор={cal}")
    for k in ("top1_full_code", "top1_okpd2", "top3", "top1_macro_by_okpd2", "attributes_accuracy", "ece",
              "fast_path_share", "seconds_per_1000"):
        print(f"  {k:22} {m[k]}")
    print(f"  по полям атрибутов     {m['attributes_by_field']}")
    print(f"ошибок: {len(m['errors'])}, подробности — {out}")


if __name__ == "__main__":
    main()
