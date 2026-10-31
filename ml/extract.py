"""Извлечение атрибутов правилами: наименование, бренд, модель, характеристики, количество, единица."""

from __future__ import annotations

import re
from pathlib import Path
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
        # Многословные и кириллические марки: ищутся по тексту целиком, длинные — раньше коротких.
        "Erich Krause", "Bruno Visconti", "Double A", "Little Doctor", "IQ Premium", "IQ Allround", "IQ Economy",
        "IQ Color", "Ballet Classic", "Ballet Universal", "Ballet Premier", "Ballet Brilliant", "Снегурочка",
        "Комус", "Офисмаг", "Гознак", "Альт", "Каляка-Маляка", "Луч", "Гамма", "Медполимер",
    )
}
_BRAND_RE = re.compile(
    r"(?<![\w])(" + "|".join(re.escape(b) for b in sorted(BRANDS, key=len, reverse=True)) + r")(?![\w])",
    re.I,
)

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
# Слова, которые не могут быть маркой, но бывают частью модели («iRU Office 313», «IdeaPad Pro 5»):
# аббревиатуры, стандарты, материалы, размеры, цвета, серии. В реальных названиях стоят там же, где марки.
NOT_BRAND_WORDS = TECH_WORDS | {
    "spi", "fifa", "pdf", "abs", "pp", "pe", "tft", "ip", "sd", "microsd", "cmos", "ccd", "dc", "ac", "uv", "eva", "pu",
    "wan", "sas", "pci", "pcie", "api", "eac", "din", "en", "ral", "gsm", "sms", "fm", "am", "tv", "mp3", "usb-c",
    "xl", "xxl", "xs", "xxs", "s", "m", "l", "rj", "led-", "smd", "ups", "atx", "itx", "rohs", "ul", "ip54", "ip65",
    "ip67", "ip68", "gost", "red", "black", "white", "silver", "yellow", "blue", "green", "grey", "gray", "gold",
    "pink", "orange", "brown", "violet", "purple", "beige", "dark", "light", "super", "maxi", "mini", "max",
    "pro", "plus", "eco", "premium", "classic", "standard", "comfort", "lite", "air", "smart", "home", "office",
    "sport", "kids", "new", "original", "extra", "ultra", "power", "basic", "set", "kit", "type", "size", "art",
    "no", "nr", "ref", "model", "series", "edition", "version", "fi", "f", "x2", "x4", "ii", "iii", "iv", "vi",
    "vii", "viii", "ix", "xi", "xii", "ethernet", "gigabit", "display", "port", "wireless", "hdr", "dlp", "lan",
}
# Кириллические аббревиатуры, которые пишут капсом, но это не марки.
CYR_ABBR = {
    "гост", "ту", "ивд", "пвх", "мдф", "дсп", "лдсп", "хдф", "рф", "ссср", "окпд", "ктру", "сте", "ип", "ооо",
    "ао", "пао", "зао", "оао", "нао", "сиз", "мфу", "ибп", "озу", "пзу", "жк", "лед", "вт", "кв", "ква", "ес",
    "снип", "сп", "сан", "пин", "ст", "мм", "см", "кг", "шт", "уп", "пэт", "пнд", "пвд", "пп", "пэ", "абс", "ппу",
    "вгс", "нгс", "эпдм", "тпр", "тпэ", "эва", "кгу", "ивл", "экг", "узи", "мрт", "кт", "пцр", "днк", "рнк",
}

_LATIN_TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9+\-./]*|\d+[A-Za-z][A-Za-z0-9\-]*")
# Слово-кандидат в марку: только буквы (и дефис/точка/& внутри), без цифр — цифры бывают у моделей.
_LATIN_WORD = re.compile(r"(?<![\w\-./])[A-Za-z][A-Za-z&]*(?:[\-.][A-Za-z]+)*(?![\w\-/])")
# Кириллица капсом: одно-три слова подряд, от трёх букв.
_CYR_CAPS = re.compile(r"(?<![\wА-ЯЁа-яё])[А-ЯЁ]{3,}(?:[\s\-][А-ЯЁ]{2,}){0,2}(?![А-ЯЁа-яё])")
_COMPAT = re.compile(r"(совместим\w*\s+с|для\s+(?:принтер\w*|мфу|ноутбук\w*|аппарат\w*|картридж\w*|техники\s+[A-Z]))\s*", re.I)
_NAME_STOP = re.compile(r"[,;:]|\.\s|\s[-–—]\s")


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
        # «USB-ресивер» — часть наименования (есть кириллица), «A4Tech», «15.6"», «M404» — уже нет.
        if re.search(r"[A-Za-z0-9\"№]", w) and not re.search(r"[А-Яа-яЁё]", w):
            break
        words.append(w)
    name = " ".join(words).strip(" -–—.")
    return name or head.strip()[:80]


# Словарь марок из карточек СТЕ (ml/build_brands.py): ~30 тыс. написаний, ищутся по словам текста.
_LEXICON_PATH = Path(__file__).resolve().parent / "models" / "brands.txt"
_LEXICON: Optional[dict[str, str]] = None
_LEX_MAX_WORDS = 4
_WORD = re.compile(r"[A-Za-zА-Яа-яЁё0-9][A-Za-zА-Яа-яЁё0-9&.+'\-]*")


def _lexicon() -> dict[str, str]:
    global _LEXICON
    if _LEXICON is None:
        _LEXICON = {}
        if _LEXICON_PATH.exists():
            for line in _LEXICON_PATH.read_text(encoding="utf-8").splitlines():
                if line and not line.startswith("#"):
                    brand = line.split("	", 1)[0]
                    _LEXICON.setdefault(brand.lower(), brand)
    return _LEXICON


def _lexicon_brand(text: str, spans: list[tuple[int, int]]) -> Optional[tuple[int, int, int, str]]:
    """Самая ранняя марка из словаря СТЕ: (начало, -длина, конец, написание из текста)."""
    lex = _lexicon()
    if not lex:
        return None
    words = [(m.start(), m.end()) for m in _WORD.finditer(text)]
    for i, (start, _) in enumerate(words):
        if _in_spans(start, spans):
            continue
        for n in range(min(_LEX_MAX_WORDS, len(words) - i), 0, -1):
            end = words[i + n - 1][1]
            piece = text[start:end]
            key = " ".join(piece.lower().split()).strip(".")
            if key in lex and key not in NOT_BRAND_WORDS:
                # Одно кириллическое слово строчными («ресурс», «гамма») — обычное слово, а не марка.
                if n == 1 and re.match(r"[а-яё]", piece):
                    continue
                return start, -(end - start), start + len(piece.rstrip(".")), piece.rstrip(".")
    return None


def _find_brand_model(text: str) -> tuple[Optional[str], Optional[str], float, float, Optional[int]]:
    """→ (бренд, модель, уверенность бренда, уверенность модели, позиция бренда в тексте)."""
    spans = _segments_excluding_compat(text)
    found = []  # (начало, -длина, конец, написание)
    for m in _BRAND_RE.finditer(text):
        if not _in_spans(m.start(), spans):
            found.append((m.start(), -(m.end() - m.start()), m.end(), BRANDS[m.group(1).lower()]))
            break
    if (hit := _lexicon_brand(text, spans)) is not None:
        found.append(hit)
    if found:
        start, _, end, brand = min(found)  # самая ранняя марка, при равенстве — длинная
        model = _model_after(text[end:])
        conf_model = 0.85 if model and _looks_like_model(model) else 0.5
        return brand, model, 0.95, conf_model, start
    # Марки нет в словаре — ищем самое раннее «марочное» слово: латиница с заглавной или капсом
    # (Rommelsbacher, REXANT, AvtoPoryadok), либо кириллица капсом (ПРАКТИК, МОНЕТНЫЙ ДВОР).
    candidates = []
    for m in _LATIN_WORD.finditer(text):
        tok = m.group(0)
        if (_in_spans(m.start(), spans) or tok.lower().strip(".") in NOT_BRAND_WORDS or len(tok) < 2
                or not (tok[:1].isupper() or tok.isupper())):
            continue
        candidates.append((m.start(), m.end(), tok, 0.6))
        break
    letters = [c for c in text if c.isalpha()]
    shouting = letters and sum(c.isupper() for c in letters) / len(letters) > 0.6  # весь текст капсом
    for m in ([] if shouting else _CYR_CAPS.finditer(text)):
        found = m.group(0).strip()
        first = found.split()[0]
        # Одно короткое слово капсом (СМЛ, АСФ, ИОЛ) — почти всегда аббревиатура, а не марка.
        if (_in_spans(m.start(), spans) or first.lower() in CYR_ABBR or (" " not in found and len(found) <= 4)
                or text[max(0, m.start() - 1):m.start()] == "("):
            continue
        candidates.append((m.start(), m.end(), found, 0.5))
        break
    if candidates:
        start, end, tok, conf = min(candidates)
        model = _model_after(text[end:])
        return tok, model, conf, (0.85 if model and _looks_like_model(model) else 0.5), start
    return None, None, 0.0, 0.0, None


_MODEL_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9+\-./]*$")


def _model_after(rest: str) -> Optional[str]:
    """Модель — латинские и цифровые слова сразу после бренда до запятой: «IdeaPad 3 15IAU7», «M6700DW».

    Голое число берём, если за ним снова латиница («IdeaPad 3 15IAU7») или оно последнее («Office 313»),
    но не перед единицей («Kingston 64 ГБ»).
    """
    words = re.split(r"[,;(]|\.\s", rest, 1)[0].split()
    parts: list[str] = []
    for j, w in enumerate(words):
        w = w.rstrip(".")
        if not _MODEL_TOKEN.match(w) or w.lower() in TECH_WORDS:
            break
        if w.isdigit():
            nxt = words[j + 1] if j + 1 < len(words) else ""
            last = j == len(words) - 1
            # «Office 313», «IdeaCentre 3» — число в конце модели; «Kingston 64 ГБ» — уже характеристика.
            if not ((last and parts) or (_MODEL_TOKEN.match(nxt) and re.search(r"[A-Za-z]", nxt)
                                         and nxt.lower() not in TECH_WORDS)):
                break
        parts.append(w)
    return " ".join(parts) or None


def _adjacent(text: str, a: int, b: int) -> bool:
    """Между токенами только пробелы (или ничего) — значит, они из одной «марки-модели»."""
    return text[a:b].strip() == ""


def _looks_like_model(tok: str) -> bool:
    return bool(re.search(r"\d", tok)) or (tok[:1].isupper() and len(tok) >= 3)


_KV = re.compile(r"([А-Яа-яЁё][^:;,]{1,60}?)\s*:\s*([^;,]+)")
# Пары через «;» (как в спецификациях и карточках СТЕ): запятая — часть значения («35,5х16 см», «Пластик, резина»).
_KV_SEMI = re.compile(r"([А-Яа-яЁё][^:;]{1,120}?)\s*:\s*([^;]+)")  # в СТЕ названия бывают по 80+ символов


def _characteristics(text: str) -> dict[str, str]:
    out = {}
    for m in (_KV_SEMI if ";" in text else _KV).finditer(text):
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
        brand, model, brand_conf, model_conf, brand_at = _find_brand_model(text)
        name_src = item.columns.get("name") or text
        if not item.columns.get("name") and brand_at:
            # «Бумага для офисной техники Снегурочка …»: наименование кончается там, где начинается марка.
            name_src = text[:brand_at]
        name = _extract_name(name_src)
        name_conf = 0.9 if item.columns.get("name") else (0.8 if len(name) >= 3 else 0.3)
        return ExtractedAttributes(
            name=name,
            brand=brand,
            model=model,
            quantity=_quantity(item.columns.get("quantity")),
            unit=(item.columns.get("unit") or None),
            # Колонка характеристик отдельно от наименования: бэкенд склеивает колонки через пробел, и пара
            # «Вид игрушки: …» иначе получала бы в ключ хвост наименования.
            characteristics=_characteristics(
                " ; ".join(v for k in ("description", "characteristics") if (v := item.columns.get(k))) or text),
            method="rules",
            field_confidence={"name": name_conf, "brand": brand_conf, "model": model_conf},
        )
