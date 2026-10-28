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
    "mm": ("мм", "миллиметр", "миллиметра", "миллиметров"),
    "cm": ("см", "сантиметр", "сантиметра", "сантиметров"),
    "m": ("м", "метр", "метров", "метра"),
    "tb": ("тб", "tb", "терабайт"),
    "gb": ("гб", "gb", "гигабайт", "гбайт"),
    "mbs": ("мб/с", "мбайт/с", "mb/s"),
    "mbps": ("мбит/с", "мбит", "mbps"),
    "mb": ("мб", "mb", "мбайт", "мегабайт"),
    "kb": ("кб", "kb", "кбайт", "килобайт"),
    "kwh": ("квт·ч", "квт.ч", "квт*ч", "квтч", "киловатт-час"),
    "wh": ("вт.ч", "втч", "вт*ч", "вт·ч", "wh", "ватт-час"),
    "mw": ("мвт", "мегаватт"),
    "kw": ("квт", "kw", "киловатт", "киловатта"),
    "w": ("вт", "w", "ватт", "ватта"),
    "hp": ("л.с.", "л.с", "лошадиных сил", "лошадиная сила"),
    "ghz": ("ггц", "ghz", "гигагерц"),
    "mhz": ("мгц", "mhz", "мегагерц"),
    "khz": ("кгц", "khz", "килогерц"),
    "hz": ("гц", "hz", "герц"),
    "bit": ("бит", "bit"),
    "ml": ("мл", "ml", "см3", "см^3", "см³", "куб.см", "кубический сантиметр", "кубических сантиметров",
           "кубических сантиметра", "миллилитр", "миллилитров"),
    "l": ("л", "литр", "литра", "литров", "дм3", "дм³", "кубический дециметр", "кубических дециметров"),
    "t": ("т", "тонн", "тонна", "тонны"),
    "kg": ("кг", "kg", "килограмм", "килограмма", "килограммов"),
    "mg": ("мг", "mg", "миллиграмм", "миллиграмма", "миллиграммов"),
    "g": ("г", "гр", "грамм", "грамма", "граммов"),
    "gauge": ("g",),
    "fr": ("fr", "сh", "ch", "френч"),
    "ms": ("мс", "миллисекунд", "миллисекунда", "миллисекунды"),
    "h": ("ч", "час", "часа", "часов"),
    "min": ("мин", "минут", "минуты", "минута"),
    "s": ("с", "сек", "секунд", "секунда", "секунды"),
    "day": ("сут", "сутки", "суток"),
    "month": ("мес", "месяц", "месяца", "месяцев"),
    "year": ("год", "года", "лет"),
    "c": ("°c", "°с", "градус цельсия", "градуса цельсия", "градусов цельсия", "град. c", "град. с"),
    "deg": ("°", "градус", "градуса", "градусов", "град"),
    "k": ("кельвин", "кельвина", "кельвинов"),
    "pct": ("%", "процент", "процента", "процентов"),
    "db": ("дб", "db", "децибел", "децибела"),
    "kv": ("кв", "kv", "киловольт"),
    "v": ("в", "вольт", "вольта", "v"),
    "va": ("ва", "va", "вольт-ампер"),
    "ah": ("а·ч", "а*ч", "ач", "ампер-час", "ампер-часов"),
    "a": ("а", "ампер", "ампера"),
    "mpa": ("мпа", "мегапаскаль"),
    "kpa": ("кпа", "килопаскаль"),
    "pa": ("па", "паскаль"),
    "bar": ("бар",),
    "atm": ("атм",),
    "j": ("дж", "джоуль", "джоулей"),
    "n": ("н", "ньютон", "ньютонов"),
    "lux": ("лк", "люкс"),
    "lm": ("лм", "люмен", "люменов"),
    "ohm": ("ом",),
    "pcs": ("шт", "штук", "штуки", "штука"),
    "sheet": ("лист", "листов", "листа"),
    "person": ("чел", "человек"),
}

# Сначала длинные варианты: «мб/с» раньше «мб», «вт.ч» раньше «вт», «градус цельсия» раньше «градус».
_ALIAS_LIST = sorted(
    ((alias, key) for key, aliases in UNIT_ALIASES.items() for alias in aliases),
    key=lambda x: -len(x[0]),
)

# Единица из поля unit характеристики КТРУ (полные названия ОКЕИ) → каноническая единица.
# Порядок важен: составные и длинные названия раньше коротких («мегабайт в секунду» раньше «мегабайт»).
_KTRU_UNITS = (
    # Объёмные и площадные — первыми, иначе «кубический сантиметр» распознается как сантиметр.
    ("кубический сантиметр", "ml"), ("кубический дециметр", "l"), ("кубический метр", None),
    ("кубический миллиметр", None), ("квадратн", None),
    ("дюйм", "inch"), ("миллиметр", "mm"), ("сантиметр", "cm"),
    ("мегабайт в секунду", "mbs"), ("мегабит в секунду", "mbps"), ("терабайт", "tb"), ("гигабайт", "gb"),
    ("килобайт", "kb"), ("мбайт", "mb"), ("мегабайт", "mb"), ("киловатт-час", "kwh"), ("ватт-час", "wh"),
    ("вт.ч", "wh"), ("мегаватт", "mw"), ("киловатт", "kw"), ("лошадиная сила", "hp"), ("гигагерц", "ghz"),
    ("мегагерц", "mhz"), ("килогерц", "khz"), ("герц", "hz"), ("ггц", "ghz"), ("мгц", "mhz"), ("кгц", "khz"),
    ("гц", "hz"), ("бит", "bit"), ("миллилитр", "ml"), ("мл", "ml"), ("децибел", "db"), ("км/ч", None),
    ("тонна в", None), ("тысяча тонн", None), ("оборот в", None), ("миллисекунда", "ms"), ("миллиграмм", "mg"),
    ("килограмм", "kg"), ("кг", "kg"), ("грамм", "g"), ("градус цельсия", "c"), ("°c", "c"),
    ("градус (плоского угла)", "deg"), ("кельвин", "k"), ("процент", "pct"), ("киловольт", "kv"),
    ("вольт-ампер", "va"), ("вольт", "v"), ("ампер-час", "ah"), ("ампер", "a"), ("мегапаскаль", "mpa"),
    ("килопаскаль", "kpa"), ("паскаль", "pa"), ("физическая атмосфера", "atm"), ("бар", "bar"), ("джоуль", "j"),
    ("ньютон", "n"), ("тонна", "t"), ("люкс", "lux"), ("люмен", "lm"), ("месяц", "month"), ("сутки", "day"), ("год", "year"),
    ("секунда", "s"), ("час", "h"), ("минута", "min"), ("мин", "min"), ("метр", "m"), ("литр", "l"),
    ("человек", "person"), ("лист", "sheet"), ("шт", "pcs"), ("ватт", "w"), ("вт", "w"), ("ом", "ohm"),
)

# Близкие единицы, которые в тексте пишут взаимозаменяемо: «30 градусов» про температуру и про угол.
COMPATIBLE_UNITS = {("c", "deg"), ("deg", "c")}


def unit_key_from_ktru(unit: Optional[str], char_name: str = "") -> Optional[str]:
    """Единица из поля `unit` характеристики КТРУ → каноническая единица или None."""
    name = char_name.rstrip().lower()
    if name.endswith(", g"):
        return "gauge"
    if name.endswith(", fr") or name.endswith(", сh") or name.endswith(", ch"):
        return "fr"
    if not unit or unit.strip() in {"-", ""}:
        return None
    u = unit.lower()
    for needle, key in _KTRU_UNITS:
        if needle in u:
            return key
    exact = {"л.": "l", "л": "l", "г": "g", "ч": "h", "с": "s", "в": "v", "т": "t"}
    return exact.get(u.strip())


def units_compatible(query_unit: Optional[str], char_unit: Optional[str]) -> bool:
    """Число из текста подходит характеристике по единице: единицы нет, она та же или близкая."""
    if query_unit is None or char_unit is None:  # единица не указана в тексте или не распознана у КТРУ
        return True
    return query_unit == char_unit or (query_unit, char_unit) in COMPATIBLE_UNITS


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
    # Для «от 12 до 13», «14-15», «не менее 17», «< 10» — интервал из текста; для голого числа — None.
    interval: Optional[Range] = None


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
_LEAD = r"(?<![\w.,\-/])"
_N = r"(\d+(?:[.,]\d+)?)"
_NUM_FIND = re.compile(_LEAD + _N)

# Порядок важен: «не более» раньше «более», «не менее» раньше «менее», «>=» раньше «>».
_INTERVALS = [
    (re.compile(rf"(?<![а-я])от\s*{_N}\s*(?:[^\d\s;,]{{1,8}}\s*)?до\s*{_N}"), "range"),
    (re.compile(rf"{_LEAD}{_N}\s*[-–—]\s*{_N}"), "range"),
    (re.compile(rf"(?:не\s+менее|не\s+меньше|не\s+ниже|>=|≥|(?<![а-я])от)\s*{_N}"), "lo_inc"),
    (re.compile(rf"(?:не\s+более|не\s+больше|не\s+выше|<=|≤|(?<![а-я])до)\s*{_N}"), "hi_inc"),
    (re.compile(rf"(?:(?<![а-я])более|(?<![а-я])больше|свыше|>)\s*{_N}"), "lo_exc"),
    (re.compile(rf"(?:(?<![а-я])менее|(?<![а-я])меньше|<)\s*{_N}"), "hi_exc"),
]


def _interval(kind: str, a: float, b: Optional[float]) -> Range:
    if kind == "range":
        return Range(min(a, b), True, max(a, b), True)
    return {
        "lo_inc": Range(lo=a, lo_inc=True),
        "lo_exc": Range(lo=a, lo_inc=False),
        "hi_inc": Range(hi=a, hi_inc=True),
        "hi_exc": Range(hi=a, hi_inc=False),
    }[kind]


def find_numbers(text_lower: str) -> list[QueryNumber]:
    """Числа и интервалы в тексте позиции с единицей, если она стоит сразу после последнего числа."""
    out: list[QueryNumber] = []
    taken: set[int] = set()  # начала чисел, уже вошедших в интервал
    for pattern, kind in _INTERVALS:
        for m in pattern.finditer(text_lower):
            starts = [m.start(g) for g in range(1, (m.lastindex or 0) + 1)]
            if any(st in taken for st in starts):
                continue
            nums = [to_float(m.group(g)) for g in range(1, (m.lastindex or 0) + 1)]
            unit, end = _match_unit(text_lower, m.end())
            rng = _interval(kind, nums[0], nums[1] if len(nums) > 1 else None)
            mid = nums[0] if len(nums) == 1 else (nums[0] + nums[1]) / 2
            out.append(QueryNumber(mid, unit, m.start(), end if unit else m.end(), rng))
            taken.update(starts)
    for m in _NUM_FIND.finditer(text_lower):
        if m.start() in taken:
            continue
        unit, end = _match_unit(text_lower, m.end())
        out.append(QueryNumber(to_float(m.group(1)), unit, m.start(), end if unit else m.end()))
    return sorted(out, key=lambda n: n.start)


def _max_lo(a, ai, b, bi):
    if a is None:
        return b, bi
    if b is None or a > b:
        return a, ai
    if b > a:
        return b, bi
    return a, ai and bi


def _min_hi(a, ai, b, bi):
    if a is None:
        return b, bi
    if b is None or a < b:
        return a, ai
    if b < a:
        return b, bi
    return a, ai and bi


def _side(a, ai, b, bi) -> float:
    if a is None and b is None:
        return 1.0
    if a is None or b is None:
        return 0.4
    if a == b:
        return 1.0 if ai == bi else 0.9
    return max(0.0, 1.0 - 3.0 * abs(a - b) / max(abs(a), abs(b), 1e-9))


def interval_match(q: Range, c: Range) -> Optional[float]:
    """Интервал из текста против условия КТРУ: None — не пересекаются, иначе близость границ 0..1."""
    lo, lo_inc = _max_lo(q.lo, q.lo_inc, c.lo, c.lo_inc)
    hi, hi_inc = _min_hi(q.hi, q.hi_inc, c.hi, c.hi_inc)
    if lo is not None and hi is not None and (lo > hi or (lo == hi and not (lo_inc and hi_inc))):
        return None
    return (_side(q.lo, q.lo_inc, c.lo, c.lo_inc) + _side(q.hi, q.hi_inc, c.hi, c.hi_inc)) / 2
