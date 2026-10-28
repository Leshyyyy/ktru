"""Живой проверочный набор категорий из карточек СТЕ (портал поставщиков Москвы, dantetemplar/zakupki-dataset).

    python -m ml.ste_eval --ste data/ktru/v2/ste.parquet --catalog data/ktru/v2/catalog_fetched.jsonl \
        --seed 8 --exclude data/ste_eval.jsonl --out data/ktru/ste/ste_dev.jsonl

Методика Таира (ml_probe/make_ste_eval.py): карточки, по которым были контракты, не больше двух на код ОКПД2,
только коды среди действующих позиций КТРУ. Эталон — ОКПД2 карточки, expected_ktru_code = «<ОКПД2>-X»,
поэтому осмысленны только метрики по ОКПД2. raw_text — название, raw_text_spec — название и до 6 характеристик.

Набор Таира (seed 7) — отложенная проверка: веса и пороги подбираются на наборе с другим seed, карточки из --exclude
в него не попадают. Модель категорий (ml.category) исключает из обучения карточки обоих наборов.
"""

from __future__ import annotations

import argparse
import collections
import json
import random
import re
from pathlib import Path

from .category import norm_name

FULL_OKPD2 = re.compile(r"^\d\d\.\d\d\.\d\d\.\d\d\d$")


def active_okpd2(catalog_path: str | Path) -> set[str]:
    """Коды ОКПД2, у которых есть действующие неукрупнённые позиции КТРУ, — только такой ответ достижим."""
    codes = set()
    with open(catalog_path, encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            if r["status"] == "active" and not r.get("is_enlarged"):
                codes.add(r["okpd2_code"])
    return codes


def load_names(paths: list[str]) -> set[str]:
    """Нормализованные названия (raw_text) из проверочных наборов — их нельзя брать в обучение и в другой набор."""
    names = set()
    for p in paths or []:
        with open(p, encoding="utf-8") as f:
            names.update(norm_name(json.loads(line)["raw_text"]) for line in f if line.strip())
    return names


def main() -> None:
    import pyarrow.parquet as pq  # noqa: PLC0415

    ap = argparse.ArgumentParser(description="Проверочный набор категорий из СТЕ")
    ap.add_argument("--ste", required=True)
    ap.add_argument("--catalog", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--n", type=int, default=1000)
    ap.add_argument("--seed", type=int, default=8)
    ap.add_argument("--exclude", nargs="*", default=[], help="наборы, карточки которых сюда не попадают")
    args = ap.parse_args()

    okpd_ok = active_okpd2(args.catalog)
    excluded = load_names(args.exclude)
    f = pq.ParquetFile(args.ste)
    cand = []
    for g in range(f.metadata.num_row_groups):
        t = f.read_row_group(g, columns=["okpd2_code", "contracts_count"])
        for i, (c, k) in enumerate(zip(t.column(0).to_pylist(), t.column(1).to_pylist())):
            if c and FULL_OKPD2.match(c) and c in okpd_ok and (k or 0) > 0:
                cand.append((g, i, c))
    rnd = random.Random(args.seed)
    by = collections.defaultdict(list)
    for x in cand:
        by[x[2]].append(x)
    groups = list(by)
    rnd.shuffle(groups)
    # Берём с запасом: часть карточек отсеется по --exclude и повторам названий.
    pick = []
    for code in groups:
        pick += rnd.sample(by[code], min(3, len(by[code])))
        if len(pick) >= args.n * 2:
            break
    need = collections.defaultdict(list)
    for k, (g, i, c) in enumerate(pick):
        need[g].append((k, i, c))
    rows = {}
    for g, lst in need.items():
        t = f.read_row_group(g, columns=["name", "characteristics"])
        for k, i, c in lst:
            name = t.column(0)[i].as_py() or ""
            ch = t.column(1)[i].as_py() or []
            spec = "; ".join(f"{x['name']}: {x['value']}" + (f" {x['unit']}" if x.get("unit") else "")
                             for x in ch[:6] if x.get("value"))
            rows[k] = (c, name, (name + ("; " + spec if spec else ""))[:600])
    out, seen, per_code = [], set(excluded), collections.Counter()
    for k in sorted(rows):
        c, name, spec = rows[k]
        key = norm_name(name)
        if not key or key in seen or per_code[c] >= 2:
            continue
        seen.add(key)
        per_code[c] += 1
        out.append({"id": f"ste{args.seed}_{k}", "raw_text": name, "raw_text_spec": spec,
                    "expected_ktru_code": c + "-X", "split": "dev"})
        if len(out) >= args.n:
            break
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fo:
        for r in out:
            fo.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"{len(out)} позиций, {len(per_code)} кодов ОКПД2 → {args.out}")


if __name__ == "__main__":
    main()
