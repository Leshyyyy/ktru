"""Числа, единицы и ограничения из значений характеристик КТРУ.

Значения характеристик в КТРУ — это либо числовые условия («≥ 17», «> 2.5 и ≤ 3», «от 8 ГБ»),
либо текст («SSD», «Да», «Full HD»). Здесь они разбираются один раз при загрузке каталога,
а числа с единицами вытаскиваются из текста позиции один раз на запрос.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional, Union

NUM_RE = r"\d+(?:[.,]\d+)?"

# Каноническая единица → как её пишут в спецификациях (в нижнем регистре).
UNIT_ALIASES: dict[str, tuple[str, ...]] = {
    "inch": ('"', "″", "''", "дюйм", "дюйма", "дюймов"),
    "mm": ("мм", "миллиметр", "миллиметров"),
    "cm": ("см", "сантиметр", "сантиметров"),
    "m": ("м", "метр", "метров", "метра"),
    "tb": ("тб", "tb", "терабайт"),
    "gb": ("гб", "gb", "гигабайт", "гбайт"),
    "mbs": ("мб/с", "мбайт/с", "mb/s"),
    "mbps": ("мбит/с", "мбит", "mbps"),
    "mb": ("мб", "mb", "мбайт", "мегабайт"),
    "wh": ("вт.ч", "втч", "вт*ч", "вт·ч", "wh"),
    "w": ("вт", "w", "ватт"),
    "ghz": ("ггц", "ghz"),
    "mhz": ("мгц", "mhz"),
    "khz": ("кгц", "khz"),
    "hz": ("гц", "hz"),
    "bit": ("бит", "bit"),
    "ml": ("мл", "ml", "см3", "см^3", "куб.см"),
    "l": ("л", "литр", "литра", "литров"),
    "kg": ("кг", "kg"),
    "g": ("г", "гр", "грамм"),
    "gauge": ("g",),
    "h": ("ч", "час", "часа", "часов"),
    "min": ("мин", "минут"),
    "s": ("с", "сек", "секунд"),
    "c": ("°c", "°с", "градус", "градусов"),
    "db": ("дб", "db", "децибел"),
    "v": ("в", "вольт", "v"),
    "ohm": ("ом",),
    "pcs": ("шт", "штук", "штуки"),
    "person": ("чел", "человек"),
}

# Сначала длинные варианты: «мб/с» раньше «мб», «вт.ч» раньше «вт».
_ALIAS_LIST = sorted(
    ((alias, key) for key, aliases in UNIT_ALIASES.items() for alias in aliases),
    key=lambda x: -len(x[0]),
)


def unit_key_from_ktru(unit: Optional[str], char_name: str = "") -> Optional[str]:
    """Единица из поля `unit` характеристики КТРУ → каноническая единица или None."""
    if char_name.rstrip().endswith(", G"):
        return "gauge"
    if not unit or unit.strip() in {"-", ""}:
        return None
    u = unit.lower()
    rules = (
        ("дюйм", "inch"), ("миллиметр", "mm"), ("сантиметр", "cm"), ("мегабайт в секунду", "mbs"),
        ("мегабит в секунду", "mbps"), ("терабайт", "tb"), ("гигабайт", "gb"), ("мбайт", "mb"),
        ("мегабайт", "mb"), ("вт.ч", "wh"), ("ггц", "ghz"), ("мгц", "mhz"), ("кгц", "khz"), ("гц", "hz"),
        ("бит", "bit"), ("мл", "ml"), ("децибел", "db"), ("км/ч", None), ("кг", "kg"), ("метр", "m"),
        ("°c", "c"), ("человек", "person"), ("шт", "pcs"), ("мин", "min"), ("вт", "w"), ("ом", "ohm"),
    )
    for needle, key in rules:
        if needle in u:
            return key
    exact = {"л.": "l", "л": "l", "г": "g", "ч": "h", "с": "s", "в": "v"}
    return exact.get(u.strip())


def to_float(s: str) -> float:
    return float(s.replace(" ", "").replace(" ", "").replace(",", "."))


@dataclass(frozen=True)
class Range:
    lo: Optional[float] = None
    lo_inc: bool = True
    hi: Optional[float] = None
    hi_inc: bool = True

    def contains(self, v: float) -> bool:
        if self.lo is not None and (v < self.lo or (v == self.lo and not self.lo_inc)):
            return False
        if self.hi is not None and (v > self.hi or (v == self.hi and not self.hi_inc)):
            return False
        return True

    def quality(self, v: float) -> float:
        """Насколько плотно значение прилегает к границе: 1 — точно, меньше — условие шире, чем нужно.

        Нужна, чтобы из «≥ 16» и «≥ 17» для диагонали 17,3 выбрать более точное «≥ 17».
        """
        if self.lo is not None and self.hi is not None:
            return 1.0
        if self.lo is not None:
            return max(0.0, 1.0 - (v - self.lo) / (abs(v) + 1e-9))
        if self.hi is not None:
            return max(0.0, 1.0 - (self.hi - v) / (abs(self.hi) + 1e-9))
        return 1.0

    def describe(self) -> str:
        if self.lo is not None and self.lo == self.hi:
            return _fmt(self.lo)
        parts = []
        if self.lo is not None:
            parts.append(("≥ " if self.lo_inc else "> ") + _fmt(self.lo))
        if self.hi is not None:
            parts.append(("≤ " if self.hi_inc else "< ") + _fmt(self.hi))
        return " и ".join(parts)


@dataclass(frozen=True)
class Text:
    raw: str
    norm: str


Constraint = Union[Range, Text]

_OP_RE = re.compile(rf"^(≥|>=|>|≤|<=|<|=|от|до|не менее|не более)?\s*({NUM_RE})\s*([^\d\s].*)?$")


def _fmt(v: float) -> str:
    return str(int(v)) if v == int(v) else str(v)


def norm_text(s: str) -> str:
    s = " ".join(s.lower().replace("ё", "е").replace("”", '"').replace("“", '"').split())
    # «А4» кириллицей и «A4» латиницей — одно и то же (в КТРУ встречаются оба написания).
    return re.sub(r"(?<![\w])[аa](?=\d)", "a", s)


def parse_constraint(value: str) -> Constraint:
    """«≥ 17» → Range(lo=17); «> 2.5 и ≤ 3» → Range(2.5, False, 3, True); «SSD» → Text."""
    v = value.strip()
    parts = re.split(r"\s+и\s+", v)
    lo = hi = None
    lo_inc = hi_inc = True
    for part in parts:
        m = _OP_RE.match(part.strip())
        if not m:
            return Text(v, norm_text(v))
        op, num, tail = m.group(1), to_float(m.group(2)), m.group(3)
        # Хвост допустим только как единица («от 8 ГБ», «от 13"»), иначе это текст вроде «1280 x 1024».
        if tail and not _match_unit(tail.strip().lower(), 0)[0]:
            return Text(v, norm_text(v))
        if op in (None, "="):
            lo = hi = num
        elif op in ("≥", ">=", "от", "не менее"):
            lo, lo_inc = num, True
        elif op == ">":
            lo, lo_inc = num, False
        elif op in ("≤", "<=", "до", "не более"):
            hi, hi_inc = num, True
        elif op == "<":
            hi, hi_inc = num, False
    return Range(lo, lo_inc, hi, hi_inc)


@dataclass(frozen=True)
class QueryNumber:
    value: float
    unit: Optional[str]
    start: int
    end: int


def _match_unit(s: str, pos: int) -> tuple[Optional[str], int]:
    """Единица, начинающаяся в s[pos:] (после пробелов), и позиция её конца."""
    i = pos
    while i < len(s) and s[i] == " ":
        i += 1
    for alias, key in _ALIAS_LIST:
        if s.startswith(alias, i):
            end = i + len(alias)
            # Буквенная единица должна кончаться на границе слова: «м» не должно съесть «мышь».
            if alias[-1].isalpha() and end < len(s) and s[end].isalpha():
                continue
            return key, end
    return None, pos


# Число не должно быть хвостом слова или модели: «M6700», «KD-178», «i5».
_NUM_FIND = re.compile(r"(?<![\w.,\-/])(\d+(?:[.,]\d+)?)")


def find_numbers(text_lower: str) -> list[QueryNumber]:
    """Все числа в тексте позиции с единицей, если она стоит сразу после числа."""
    out = []
    for m in _NUM_FIND.finditer(text_lower):
        unit, end = _match_unit(text_lower, m.end())
        out.append(QueryNumber(to_float(m.group(1)), unit, m.start(), end if unit else m.end()))
    return out
