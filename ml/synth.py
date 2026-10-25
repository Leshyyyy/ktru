"""Синтетический dev-набор: закупочные тексты, собранные по характеристикам кода КТРУ.

    python -m ml.synth --candidates data/ktru/ktru_data/eval_dev_candidates.jsonl --out data/ktru/eval/dev_synth.jsonl

Это временная замена ручной разметке, чтобы конвейер и метрики работали с первого дня.
Тексты строятся из тех же характеристик, по которым потом идёт сверка, поэтому цифры на синтетике
оптимистичны. Для отчёта и защиты — только размеченные людьми тексты. На test генератор не запускается.
"""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

from .catalog import CatalogChar, load_jsonl
from .values import Range, Text

UNIT_TEXT = {
    "inch": ['"', " дюйм", " дюймов"], "mm": [" мм"], "cm": [" см"], "m": [" м"], "gb": [" ГБ", " Гб"],
    "tb": [" ТБ"], "mb": [" МБ"], "mbs": [" МБ/с"], "mbps": [" Мбит/с"], "wh": [" Вт·ч"], "w": [" Вт"],
    "ghz": [" ГГц"], "mhz": [" МГц"], "khz": [" кГц"], "hz": [" Гц"], "bit": [" бит"], "ml": [" мл"],
    "l": [" л"], "kg": [" кг"], "g": [" г"], "gauge": ["G"], "h": [" ч"], "min": [" мин"], "s": [" с"],
    "c": [" °C"], "db": [" дБ"], "v": [" В"], "ohm": [" Ом"], "pcs": [" шт"], "person": [" чел"],
}
BRANDS_BY_PREFIX = {
    "26.20.11": ["Lenovo", "HP", "Asus", "Huawei", "Aquarius", "iRU", "Acer"],
    "26.20.17": ["AOC", "Philips", "Samsung", "LG", "Iiyama", "BenQ"],
    "26.20.15": ["Aquarius", "iRU", "Kraftway", "Graviton", "Lenovo"],
    "26.20.16": ["Pantum", "Canon", "HP", "Kyocera", "Katusha", "Logitech", "Defender"],
    "26.30": ["TP-Link", "Huawei", "Eltex", "Yealink", "Hikvision"],
    "26.60": ["Omron", "AND", "Microlife", "B.Well"],
    "17.12": ["Svetocopy", "Ballet", "Navigator", "Maestro"],
    "22.29": ["Brauberg", "Attache", "Comix", "Deli"],
    "32.50": ["Vogt", "Apexmed", "B.Braun", "SFM"],
    "31.01": ["Chairman", "Brabix", "Helmi"],
}


def _brand_for(okpd2: str, rnd: random.Random) -> str | None:
    for prefix, brands in sorted(BRANDS_BY_PREFIX.items(), key=lambda x: -len(x[0])):
        if okpd2.startswith(prefix):
            return rnd.choice(brands)
    return None


def _model(rnd: random.Random) -> str:
    letters = "ABCDEFGHKLMNPRSTVXZ"
    style = rnd.random()
    if style < 0.4:
        return "".join(rnd.choices(letters, k=rnd.randint(1, 2))) + str(rnd.randint(100, 9999)) + "".join(
            rnd.choices(letters, k=rnd.randint(0, 2)))
    if style < 0.7:
        word = rnd.choice(["Pro", "Vision", "Office", "Smart", "Neo", "Line", "Expert"])
        return f"{word} {rnd.randint(2, 9)}{rnd.choice(letters)}{rnd.randint(10, 99)}"
    return f"{rnd.choice(letters)}{rnd.choice(letters)}-{rnd.randint(10, 999)}"


def _num(v: float) -> str:
    if v == int(v):
        return str(int(v))
    return f"{v:.2f}".rstrip("0").rstrip(".")


def _value_in(r: Range, rnd: random.Random) -> tuple[float, bool]:
    """Число, удовлетворяющее условию, и признак «это сама граница» (для формулировки «не менее»)."""
    step = 1.0 if all(x is None or x == int(x) for x in (r.lo, r.hi)) else 0.1
    if r.lo is not None and r.hi is not None:
        if r.lo == r.hi:
            return r.lo, False
        lo = r.lo if r.lo_inc else r.lo + step
        hi = r.hi if r.hi_inc else r.hi - step
        v = lo + (hi - lo) * rnd.uniform(0.2, 0.8)
        return (round(v) if step == 1.0 else round(v, 2)), False
    if r.lo is not None:
        if r.lo_inc and rnd.random() < 0.6:
            return r.lo, True
        return r.lo + step * rnd.randint(1, 3), False
    if r.hi is not None:
        if r.hi_inc and rnd.random() < 0.6:
            return r.hi, True
        return max(0.0, r.hi - step), False
    return 1.0, False


def _render_char(c: CatalogChar, rnd: random.Random) -> tuple[str, bool] | None:
    """→ (фрагмент текста, название характеристики опущено)."""
    if not c.constraints:
        return None
    con = rnd.choice(c.constraints)
    name = c.name.split(",")[0]
    lc = name[:1].lower() + name[1:]
    if isinstance(con, Range):
        v, is_bound = _value_in(con, rnd)
        unit = rnd.choice(UNIT_TEXT[c.unit_key]) if c.unit_key in UNIT_TEXT else ""
        if c.unit_key and rnd.random() < 0.3:
            return f"{_num(v)}{unit}", True
        if is_bound and con.lo is not None:
            return f"{name} {rnd.choice(['не менее', 'от'])} {_num(v)}{unit}", False
        if is_bound and con.hi is not None:
            return f"{name} {rnd.choice(['не более', 'до'])} {_num(v)}{unit}", False
        return rnd.choice([f"{name} {_num(v)}{unit}", f"{name}: {_num(v)}{unit}"]), False
    assert isinstance(con, Text)
    if con.norm == "да":
        return rnd.choice([name, f"{name}: да", f"{name} есть"]), False
    if con.norm == "нет":
        return rnd.choice([f"без {lc}", f"{name}: нет"]), False
    if rnd.random() < 0.25:
        return con.raw, True
    return rnd.choice([f"{name}: {con.raw}", f"{name} {con.raw}"]), False


def generate(catalog_path: str, candidates_path: str, per_code: int, seed: int, split: str) -> list[dict]:
    rnd = random.Random(seed)
    by_code = {e.ktru_code: e for e in load_jsonl(catalog_path)}
    codes = [json.loads(line)["ktru_code"] for line in open(candidates_path, encoding="utf-8") if line.strip()]
    out = []
    for code in codes:
        e = by_code.get(code)
        if e is None or not e.searchable:
            continue
        for _ in range(per_code):
            name = e.name.split(",")[0] if rnd.random() < 0.5 else e.name
            if rnd.random() < 0.2:
                name = name[:1].lower() + name[1:]
            brand = _brand_for(e.okpd2_code, rnd) if rnd.random() < (0.6 if e.okpd2_code.startswith("26.") else 0.3) else None
            model = _model(rnd) if brand else None
            head = name + (f" {brand} {model}" if brand else "")
            frags, hidden = [], False
            req = [c for c in e.chars if c.required]
            opt = [c for c in e.chars if not c.required]
            if len(req) > 2 and rnd.random() < 0.15:
                req.remove(rnd.choice(req))
                hidden = True
            chosen = req + rnd.sample(opt, k=min(len(opt), rnd.randint(0, 2)))
            rnd.shuffle(chosen)
            for c in chosen:
                r = _render_char(c, rnd)
                if r:
                    frags.append(r[0])
                    hidden |= r[1]
            sep = rnd.choice([", ", "; ", ", "])
            text = sep.join([head] + frags)
            out.append({
                "id": f"syn-{split}-{len(out) + 1:04d}",
                "split": split,
                "raw_text": text,
                "expected_ktru_code": code,
                "expected_attrs": {"name": name, "brand": brand, "model": model},
                "difficulty": "hard" if hidden else ("medium" if len(e.chars) > 3 else "easy"),
                "source": "synthetic: собрано по характеристикам КТРУ (ml/synth.py), не ручная разметка",
            })
    return out


def main() -> None:
    from .config import DEFAULT_CATALOG  # noqa: PLC0415

    ap = argparse.ArgumentParser(description="Синтетический dev-набор по характеристикам КТРУ")
    ap.add_argument("--catalog", default=str(DEFAULT_CATALOG))
    ap.add_argument("--candidates", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--per-code", type=int, default=2)
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()
    if "test" in Path(args.candidates).name:
        raise SystemExit("На test синтетику не строим: test размечают люди и не используют при разработке.")
    rows = generate(args.catalog, args.candidates, args.per_code, args.seed, "dev")
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"{len(rows)} синтетических позиций → {args.out}")


if __name__ == "__main__":
    main()
