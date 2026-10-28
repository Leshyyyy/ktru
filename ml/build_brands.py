"""Словарь марок из карточек СТЕ (портал поставщиков Москвы, датасет dantetemplar/zakupki-dataset).

    python -m ml.build_brands --ste data/ktru/v2/ste.parquet --catalog data/ktru/v2/catalog_full.jsonl \
        --exclude data/ktru/v2/ste_attrs_eval.jsonl --out ml/models/brands.txt

Марка попадает в словарь, если её название из поля manufacturer встречается в самих названиях карточек не реже
MIN_CARDS раз. Отсеиваются общие слова: они есть в наименованиях позиций КТРУ или в списке стоп-слов.
Карточки из --exclude (проверочный набор) в расчёт не берутся, чтобы метрика бренда не подглядывала в ответы.
"""

from __future__ import annotations

import argparse
import collections
import json
import re
from pathlib import Path

MIN_CARDS = 3
STOP = {
    "нет", "без бренда", "без марки", "отсутствует", "не указан", "не указано", "россия", "рф", "китай", "китай кнр",
    "кнр", "ооо", "оао", "зао", "ао", "пао", "ип", "прочие", "другое", "noname", "no name", "oem", "generic",
    "standard", "стандарт", "эконом", "люкс", "премиум", "classic", "eco", "pro", "plus", "mini", "maxi", "мини",
    "макси", "белый", "черный", "серый", "синий", "красный", "зеленый", "набор", "комплект", "товар", "изделие",
}


def _norm(s: str) -> str:
    s = re.sub(r"\s+", " ", s.replace("«", "").replace("»", "").replace('"', "").strip())
    return s.strip(" .,-")


def common_words(catalog_path: Path) -> set[str]:
    """Кириллические слова из наименований и характеристик КТРУ: «Гамма», «Ресурс» как марка совпали бы с обычным словом."""
    words = set()
    with open(catalog_path, encoding="utf-8") as f:
        for line in f:
            row = json.loads(line)
            texts = [row["name"]] + [c["name"] for c in row["characteristics"]]
            texts += [v for c in row["characteristics"] for v in c["values"] if not any(ch.isdigit() for ch in v)]
            for t in texts:
                for w in re.findall(r"[А-Яа-яЁё]+", t):  # только кириллица: латинские марки (HP, Bosch) не трогаем
                    words.add(w.lower())
    return words


def main() -> None:
    import pyarrow.parquet as pq  # noqa: PLC0415

    ap = argparse.ArgumentParser(description="Словарь марок из СТЕ")
    ap.add_argument("--ste", required=True)
    ap.add_argument("--catalog", required=True)
    ap.add_argument("--exclude", help="JSONL с raw_text, которые не участвуют в построении словаря")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    excluded = set()
    if args.exclude:
        excluded = {json.loads(line)["raw_text"].strip() for line in open(args.exclude, encoding="utf-8") if line.strip()}
    common = common_words(Path(args.catalog))

    hits: collections.Counter[str] = collections.Counter()
    spelled: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    pf = pq.ParquetFile(args.ste)
    for batch in pf.iter_batches(batch_size=200_000, columns=["name", "manufacturer"]):
        for name, maker in zip(batch.column("name").to_pylist(), batch.column("manufacturer").to_pylist()):
            if not name or not maker or name.strip() in excluded:
                continue
            m = _norm(maker)
            key = m.lower()
            if len(m) < 2 or key in STOP or re.fullmatch(r"[\d\W_]+", m):
                continue
            # Марка должна стоять в названии отдельным словом, а не быть частью другого.
            pos = name.lower().find(key)
            if pos < 0 or (pos > 0 and name[pos - 1].isalnum()) or (
                    pos + len(key) < len(name) and name[pos + len(key)].isalnum()):
                continue
            hits[key] += 1
            spelled[key][name[pos:pos + len(key)]] += 1

    brands = []
    for key, n in hits.items():
        if n < MIN_CARDS:
            continue
        if " " not in key and key in common:  # одно обычное слово из наименований КТРУ
            continue
        if len(key) < 3 and not key.isascii():
            continue
        brands.append((n, spelled[key].most_common(1)[0][0]))
    brands.sort(key=lambda x: (-x[0], x[1].lower()))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        f.write("# Марки из названий карточек СТЕ (dantetemplar/zakupki-dataset), не реже "
                f"{MIN_CARDS} карточек. Формат: написание<TAB>число карточек. Собрано ml/build_brands.py\n")
        for n, b in brands:
            f.write(f"{b}\t{n}\n")
    print(f"марок: {len(brands)} → {out} (исключено проверочных карточек: {len(excluded)})")


if __name__ == "__main__":
    main()
