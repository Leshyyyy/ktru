"""Точность извлечения бренда на реальных названиях товаров (ste_attrs_eval.jsonl: raw_text, brand).

    python -m ml.eval_brands --data data/ktru/v2/ste_attrs_eval.jsonl

Поле model в этом файле — полное название карточки СТЕ, а не модель, поэтому здесь считается только бренд.
"""

from __future__ import annotations

import argparse
import json
import random

from contracts.ml_contracts import RawItem

from .extract import RulesExtractor


def _norm(v) -> str:
    return " ".join(str(v or "").lower().replace("ё", "е").replace('"', "").replace("«", "").replace("»", "").split())


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--show", type=int, default=0, help="показать столько случайных ошибок")
    args = ap.parse_args()
    rows = [json.loads(line) for line in open(args.data, encoding="utf-8") if line.strip()]
    ex = RulesExtractor()
    ok, errors = 0, []
    for r in rows:
        got = ex.extract_one(RawItem(item_id="x", text=r["raw_text"])).brand
        if _norm(got) == _norm(r["brand"]):
            ok += 1
        else:
            errors.append((r["raw_text"], r["brand"], got))
    print(f"бренд: {ok}/{len(rows)} = {ok / len(rows):.3f}")
    for t, gold, got in random.Random(1).sample(errors, min(args.show, len(errors))):
        print(f"  {t[:90]!r}  эталон={gold!r}  получено={got!r}")


if __name__ == "__main__":
    main()
