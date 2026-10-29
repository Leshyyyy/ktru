"""Извлечение атрибутов правилами: наименование, бренд, модель, характеристики, количество, единица."""

from __future__ import annotations

import re
from typing import Optional

from contracts.ml_contracts import ExtractedAttributes, RawItem

from .values import to_float

BRANDS = {
    b.lower(): b
    for b in (
        "Lenovo", "HP", "Hewlett-Packard", "Dell", "Asus", "Acer", "Apple", "Huawei", "Honor", "Samsung",
        "Xiaomi", "MSI", "Gigabyte", "Aquarius", "iRU", "Irbis", "DEXP", "Digma", "Prestigio", "Haier", "Machenike",
        "Graviton", "Kraftway", "Yadro", "Fplus", "AOC", "Philips", "LG", "BenQ", "ViewSonic", "Iiyama", "NEC",
        "Pantum", "Canon", "Epson", "Brother", "Kyocera", "Xerox", "Ricoh", "Sharp", "Katusha", "Sindoh",
        "Logitech", "Defender", "Oklick", "Genius", "A4Tech", "Sven", "Gembird", "Hama", "Kingston", "Transcend",
        "SanDisk", "Seagate", "WD", "Toshiba", "Crucial", "Intel", "AMD", "Nvidia", "Ippon", "APC", "Powercom",
        "Eaton", "TP-Link", "D-Link", "Keenetic", "Mikrotik", "Cisco", "Eltex", "Hikvision", "Dahua", "Jabra",
        "Plantronics", "Panasonic", "Yealink", "Grandstream", "BIC", "Brauberg", "Attache", "Berlingo", "Stabilo",
        "Pilot", "Parker", "Schneider", "Svetocopy", "SvetoCopy", "Ballet", "IQ", "Xerox", "Navigator", "Maestro",
        "Comix", "Deli", "Kores", "Bruno Visconti", "Hatber", "Erich Krause", "B.Braun", "Braun", "BD", "Vogt",
        "SFM", "Apexmed", "Medline", "Polymed", "Romed", "Omron", "AND", "Microlife", "B.Well", "Little Doctor",
        "Mindray", "Philips", "Dräger", "Draeger", "Covidien", "Terumo", "Nipro", "Vitrum", "Bormioli", "Chairman",
        "Бюрократ", "Brabix", "Helmi", "Riva", "Metta", "Samurai",
    )
}

# Латинские слова, которые не являются брендом: интерфейсы, стандарты, технологии.
TECH_WORDS = {
    "usb", "hdmi", "vga", "dvi", "dp", "displayport", "ssd", "hdd", "sshd", "emmc", "nvme", "sata", "ddr", "ddr3",
    "ddr4", "ddr5", "gddr6", "wi-fi", "wifi", "bluetooth", "lan", "rj-45", "rj45", "ips", "va", "tn", "oled", "led",
    "lcd", "full", "hd", "uhd", "fhd", "qhd", "wuxga", "wqxga", "4k", "type-c", "a4", "a3", "a5", "a0", "a1", "a2",
    "cmyk", "rgb", "pc", "cpu", "gpu", "ram", "rom", "os", "windows", "linux", "android", "ios", "lte", "3g", "4g", "5g", "nfc", "gps", "ups", "poe", "sim", "id", "cd", "dvd", "x", "vesa",
    "g", "mm", "cm", "gb", "tb", "mb", "w", "hz", "ghz", "mhz", "luer", "lock", "pvc", "pet", "ce", "iso", "intel",
    "amd", "core", "ryzen", "celeron", "pentium", "athlon", "xeon", "i3", "i5", "i7", "i9",
}
TECH_WORDS -= {"intel", "amd"}  # бывают брендами системных блоков, но не моделей ноутбука — решает контекст ниже

_LATIN_TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9+\-./]*|\d+[A-Za-z][A-Za-z0-9\-]*")
_COMPAT = re.compile(r"(совместим\w*\s+с|для\s+(?:принтер\w*|мфу|ноутбук\w*|аппарат\w*|картридж\w*|техники\s+[A-Z]))\s*", re.I)
_NAME_STOP = re.compile(r"[,;:]|\s[-–—]\s")


def _segments_excluding_compat(text: str) -> list[tuple[int, int]]:
    """Участки «совместим с HP LaserJet …» до запятой: оттуда бренд и модель не берём."""
    spans = []
    for m in _COMPAT.finditer(text):
        end = text.find(",", m.end())
        spans.append((m.start(), end if end != -1 else len(text)))
    return spans


def _in_spans(pos: int, spans: list[tuple[int, int]]) -> bool:
    return any(a <= pos < b for a, b in spans)


_CYR_PHRASE = re.compile(r"^[а-яё][а-яё\s\-]*$")


def _extract_name(source: str) -> str:
    segments = _NAME_STOP.split(source)
    head = segments[0]
    # «Монитор, подключаемый к компьютеру», «Перчатки …, неопудренные»: короткое уточнение со строчной
    # буквы без цифр, латиницы и двоеточия — часть наименования, а не характеристика.
    for seg in segments[1:3]:
        seg = seg.strip()
        if _CYR_PHRASE.match(seg) and len(seg.split()) <= 4 and not seg.startswith(("без ", "с ")):
            head += ", " + seg
        else:
            break
    if not re.search(r"[A-Za-z0-9]", head) and len(segments) > 1:
        return head.strip(" -–—.")
    words = []
    for w in head.split():
        if re.search(r"[A-Za-z0-9\"№]", w):
            break
        words.append(w)
    name = " ".join(words).strip(" -–—.")
    return name or head.strip()[:80]


def _find_brand_model(text: str) -> tuple[Optional[str], Optional[str], float, float]:
    spans = _segments_excluding_compat(text)
    tokens = [(m.group(0), m.start(), m.end()) for m in _LATIN_TOKEN.finditer(text)]
    for i, (tok, start, end) in enumerate(tokens):
        if _in_spans(start, spans):
            continue
        key = tok.lower().strip(".")
        known = key in BRANDS
        # Неизвестный бренд: слово с заглавной буквы, только буквы, за ним идёт модель.
        # Неизвестный бренд: слово с заглавной буквы (не аббревиатура вроде SPI), за ним модель с цифрами.
        guessed = (
            not known
            and key not in TECH_WORDS
            and tok[:1].isupper()
            and tok.isalpha()
            and len(tok) >= 3
            and not (tok.isupper() and len(tok) <= 4)
            and i + 1 < len(tokens)
            and re.search(r"\d", tokens[i + 1][0]) is not None
            and _adjacent(text, end, tokens[i + 1][1])
        )
        if not (known or guessed):
            continue
        model = _model_after(text[end:])
        brand = BRANDS.get(key, tok)
        return brand, model, (0.95 if known else 0.6), (0.85 if model and _looks_like_model(model) else 0.5)
    return None, None, 0.0, 0.0


_MODEL_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9+\-./]*$")


def _model_after(rest: str) -> Optional[str]:
    """Модель — латинские и цифровые слова сразу после бренда до запятой: «IdeaPad 3 15IAU7», «M6700DW».

    Голое число берём, только если за ним снова латиница: «IdeaPad 3 15IAU7» — да, «Kingston 64 ГБ» — нет.
    """
    words = re.split(r"[,;(]", rest, 1)[0].split()
    parts: list[str] = []
    for j, w in enumerate(words):
        w = w.rstrip(".")
        if not _MODEL_TOKEN.match(w) or w.lower() in TECH_WORDS:
            break
        if w.isdigit():
            nxt = words[j + 1] if j + 1 < len(words) else ""
            if not (_MODEL_TOKEN.match(nxt) and re.search(r"[A-Za-z]", nxt) and nxt.lower() not in TECH_WORDS):
                break
        parts.append(w)
    return " ".join(parts) or None


def _adjacent(text: str, a: int, b: int) -> bool:
    """Между токенами только пробелы (или ничего) — значит, они из одной «марки-модели»."""
    return text[a:b].strip() == ""


def _looks_like_model(tok: str) -> bool:
    return bool(re.search(r"\d", tok)) or (tok[:1].isupper() and len(tok) >= 3)


_KV = re.compile(r"([А-Яа-яЁё][^:;,]{1,60}?)\s*:\s*([^;,]+)")


def _characteristics(text: str) -> dict[str, str]:
    out = {}
    for m in _KV.finditer(text):
        key = m.group(1).strip()
        if len(out) < 30 and key.lower() not in ("наименование", "товар"):
            out[key] = m.group(2).strip()
    return out


def _quantity(raw: Optional[str]) -> Optional[float]:
    if not raw:
        return None
    m = re.search(r"\d+(?:[.,]\d+)?", raw.replace(" ", " ").replace(" ", ""))
    return to_float(m.group(0)) if m else None


class RulesExtractor:
    """AttributeExtractor на правилах. Без сети и моделей."""

    method = "rules"

    def extract_batch(self, items: list[RawItem]) -> list[ExtractedAttributes]:
        return [self.extract_one(it) for it in items]

    def extract_one(self, item: RawItem) -> ExtractedAttributes:
        text = " ".join(item.text.replace(" ", " ").replace("”", '"').replace("“", '"').split())
        name_src = item.columns.get("name") or text
        name = _extract_name(name_src)
        brand, model, brand_conf, model_conf = _find_brand_model(text)
        name_conf = 0.9 if item.columns.get("name") else (0.8 if len(name) >= 3 else 0.3)
        return ExtractedAttributes(
            name=name,
            brand=brand,
            model=model,
            quantity=_quantity(item.columns.get("quantity")),
            unit=(item.columns.get("unit") or None),
            characteristics=_characteristics(text),
            method="rules",
            field_confidence={"name": name_conf, "brand": brand_conf, "model": model_conf},
        )
