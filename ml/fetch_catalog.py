"""Официальный каталог КТРУ (выгрузка ЕИС) из открытого датасета Hugging Face → JSONL в формате CatalogRow.

    python -m ml.fetch_catalog --out data/ktru/catalog.jsonl

Каталог не хранится в репозитории: его скачивают при сборке или первом запуске. Версия файла закреплена
(ревизия датасета и sha256), поэтому у всех — у команды, в CI и у проверяющих — один и тот же каталог.
Источник: dantetemplar/zakupki-dataset, подвыборка ktru (выгрузка ЕИС nsiKTRUNew_all). Лицензия датасета
не указана; данные КТРУ публикует ЕИС. Преобразование повторяет hf_convert.py из пакета данных Егора.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
import urllib.request
from pathlib import Path

DATASET = "dantetemplar/zakupki-dataset"
REVISION = "3a5fa34af05dae91ed3677de71aa65fc014e5de5"
FILE = "data/ktru-actual.parquet"
PARQUET_SHA256 = "ddad51c7a6287eb5f24fb18d9220703a66d0d6c06f999618450393870f11eb75"
URL = f"https://huggingface.co/datasets/{DATASET}/resolve/{REVISION}/{FILE}"

OPS = {"greaterOrEqual": "≥", "greater": ">", "lessOrEqual": "≤", "less": "<", "equal": "="}


def _range_text(r: dict) -> str:
    lo, hi = r.get("min"), r.get("max")
    lo_op, hi_op = OPS.get(r.get("min_operator") or "", ""), OPS.get(r.get("max_operator") or "", "")
    if lo is not None and hi is not None:
        return f"{lo_op} {lo} и {hi_op} {hi}".replace("= ", "")
    if lo is not None:
        return f"{lo_op} {lo}".strip().replace("= ", "")
    if hi is not None:
        return f"{hi_op} {hi}".strip().replace("= ", "")
    return ""


def _char(ch: dict) -> dict:
    values, seen, unit, kinds = [], set(), None, set()
    for v in ch.get("values") or []:
        if v.get("unit") and not unit:
            unit = v["unit"].get("name")
        texts = []
        if v.get("text"):
            texts.append(str(v["text"]).strip())
            kinds.add("choice")
        for r in v.get("ranges") or []:
            if t := _range_text(r):
                texts.append(t)
                kinds.add("range")
        cv = v.get("concrete_values")
        if cv:
            for c in cv if isinstance(cv, list) else [cv]:
                t = (c.get("value") or c.get("text")) if isinstance(c, dict) else c
                if t not in (None, ""):
                    texts.append(str(t))
                    kinds.add("choice")
        for t in texts:
            if t and t not in seen:
                seen.add(t)
                values.append(t)
    return {"name": (ch.get("name") or "").strip(), "values": values, "required": bool(ch.get("required")),
            "unit": unit, "kind": next(iter(kinds)) if len(kinds) == 1 else None}


def convert_row(row: dict) -> dict:
    chars = [_char(c) for c in (row.get("characteristics") or []) if c.get("actual", True)]
    units = row.get("units") or []
    return {
        "ktru_code": row["code"],
        "okpd2_code": (row.get("okpd2") or {}).get("code") or "",
        "name": row.get("name") or "",
        "description": row.get("description") or None,
        "unit": units[0].get("name") if units else None,
        "status": "active" if row.get("status") == "ACTIVE" else "archived",
        "is_enlarged": bool(row.get("is_template")),
        "replaced_by": None,
        "characteristics": [c for c in chars if c["name"]],
    }


def download(dest: Path, expected_sha256: str | None) -> None:
    with urllib.request.urlopen(URL, timeout=300) as r, open(dest, "wb") as f:  # noqa: S310 — фиксированный https
        digest = hashlib.sha256()
        while chunk := r.read(1 << 20):
            f.write(chunk)
            digest.update(chunk)
    got = digest.hexdigest()
    if expected_sha256 and got != expected_sha256:
        raise SystemExit(f"sha256 не совпал: ожидали {expected_sha256}, получили {got}. Файл в датасете изменился.")


def main() -> None:
    ap = argparse.ArgumentParser(description="Скачать официальный КТРУ и сохранить как CatalogRow JSONL")
    ap.add_argument("--out", required=True)
    ap.add_argument("--parquet", help="уже скачанный parquet (без сети)")
    ap.add_argument("--sha256", default=PARQUET_SHA256)
    args = ap.parse_args()
    import pyarrow.parquet as pq  # noqa: PLC0415 — нужен только здесь

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(args.parquet) if args.parquet else Path(tmp) / "ktru.parquet"
        if not args.parquet:
            print(f"Скачиваю {URL}", file=sys.stderr)
            download(src, args.sha256)
        table = pq.read_table(src)
        n = active = 0
        with open(out, "w", encoding="utf-8") as f:
            for batch in table.to_batches(max_chunksize=5000):
                for row in batch.to_pylist():
                    rec = convert_row(row)
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    n += 1
                    active += rec["status"] == "active"
    print(f"Каталог: {n} позиций, активных {active} → {out}", file=sys.stderr)


if __name__ == "__main__":
    main()
