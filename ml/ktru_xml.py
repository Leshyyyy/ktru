"""Официальная выгрузка КТРУ ЕИС (nsiKTRUNew, XML в zip) → JSONL в формате CatalogRow.

    python -m ml.ktru_xml --src <папка с nsiKTRUNew_*.xml.zip> --out data/ktru/catalog_eis.jsonl

Берутся полные выгрузки all_actual и all_not-actual, затем по порядку времени накатываются инкременты
inc_actual / inc_not-actual: более поздняя версия позиции заменяет раннюю. Преобразование значений
характеристик то же, что в ml.fetch_catalog, чтобы оба источника давали одинаковые строки.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from .fetch_catalog import OPS

NS = {"oos": "http://zakupki.gov.ru/oos/types/1"}
_TIME = re.compile(r"_(\d{14})")


def _t(el, path: str):
    found = el.find(path, NS)
    return found.text.strip() if found is not None and found.text else None


def _range_text(r) -> str:
    lo, hi = _t(r, "oos:min"), _t(r, "oos:max")
    lo_op = OPS.get(_t(r, "oos:minMathNotation") or "", "")
    hi_op = OPS.get(_t(r, "oos:maxMathNotation") or "", "")
    if lo is not None and hi is not None:
        return f"{lo_op} {lo} и {hi_op} {hi}".replace("= ", "")
    if lo is not None:
        return f"{lo_op} {lo}".strip().replace("= ", "")
    if hi is not None:
        return f"{hi_op} {hi}".strip().replace("= ", "")
    return ""


def _char(ch) -> dict | None:
    if (_t(ch, "oos:actual") or "true") != "true":
        return None
    values, seen, unit, kinds = [], set(), None, set()
    for v in ch.findall("oos:values/oos:value", NS):
        if unit is None:
            unit = _t(v, "oos:OKEI/oos:name")
        texts = []
        if (q := _t(v, "oos:qualityDescription")) is not None:
            texts.append(q)
            kinds.add("choice")
        for r in v.findall("oos:rangeSet/oos:valueRange", NS):
            if t := _range_text(r):
                texts.append(t)
                kinds.add("range")
        for c in v.findall("oos:valueSet/oos:concreteValue", NS):
            if c.text and c.text.strip():
                texts.append(c.text.strip())
                kinds.add("choice")
        for t in texts:
            if t and t not in seen:
                seen.add(t)
                values.append(t)
    name = (_t(ch, "oos:name") or "").strip()
    if not name:
        return None
    return {"name": name, "values": values, "required": _t(ch, "oos:isRequired") == "true",
            "unit": unit, "kind": next(iter(kinds)) if len(kinds) == 1 else None}


def position_row(pos) -> tuple[str, int, dict]:
    data = pos.find("oos:data", NS)
    code = _t(data, "oos:code")
    okeis = data.findall("oos:OKEIs/oos:OKEI", NS)
    chars = [c for c in (_char(ch) for ch in data.findall("oos:characteristics/oos:characteristic", NS)) if c]
    row = {
        "ktru_code": code,
        "okpd2_code": _t(data, "oos:OKPD2/oos:code") or "",
        "name": _t(data, "oos:name") or "",
        "description": _t(data, "oos:nsiDescription"),
        "unit": _t(okeis[0], "oos:name") if okeis else None,
        "status": "active" if _t(data, "oos:status") == "ACTIVE" else "archived",
        "is_enlarged": _t(data, "oos:isTemplate") == "true",
        "replaced_by": None,
        "characteristics": chars,
    }
    return code, int(_t(data, "oos:version") or 0), row


def read_zip(path: Path):
    with zipfile.ZipFile(path) as z:
        for name in z.namelist():
            with z.open(name) as f:
                for _, el in ET.iterparse(f):
                    if el.tag == f"{{{NS['oos']}}}position":
                        yield position_row(el)
                        el.clear()


def build(src: Path) -> dict[str, dict]:
    files = sorted(src.glob("nsiKTRUNew_*.xml.zip"))
    full = [f for f in files if "_all_" in f.name]
    inc = sorted((f for f in files if "_inc_" in f.name), key=lambda f: _TIME.findall(f.name)[-1])
    rows: dict[str, tuple[int, dict]] = {}
    for f in full + inc:  # полные выгрузки, затем инкременты по времени окончания периода
        for code, version, row in read_zip(f):
            if code not in rows or version >= rows[code][0]:
                rows[code] = (version, row)
    return {code: row for code, (_, row) in rows.items()}


def main() -> None:
    ap = argparse.ArgumentParser(description="Официальный КТРУ ЕИС (XML) → CatalogRow JSONL")
    ap.add_argument("--src", required=True, help="папка с nsiKTRUNew_*.xml.zip")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    rows = build(Path(args.src))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        for code in sorted(rows):
            f.write(json.dumps(rows[code], ensure_ascii=False) + "\n")
    active = sum(r["status"] == "active" for r in rows.values())
    print(f"Каталог ЕИС: {len(rows)} позиций, активных {active} → {out}", file=sys.stderr)


if __name__ == "__main__":
    main()
