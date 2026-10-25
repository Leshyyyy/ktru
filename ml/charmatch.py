"""Сверка характеристик позиции с характеристиками кода КТРУ.

Внутри одного кода ОКПД2 позиции КТРУ часто называются одинаково («Ноутбук», «Игла спинальная»)
и различаются только обязательными характеристиками: «Диагональ экрана ≥ 17» против «≥ 16».
Поэтому выбор внутри группы делает этот модуль, а не поиск по тексту.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from contracts.ml_contracts import CharacteristicMatch

from .catalog import Catalog, CatalogChar, CatalogEntry
from .values import QueryNumber, Range, Text, find_numbers, interval_match, norm_text

W_REQUIRED = 1.0
W_OPTIONAL = 0.35
# Сглаживание: позиция, у которой совпало больше характеристик, при равной доле совпадений выше.
EVIDENCE_PRIOR = 0.5
ANCHOR_WINDOW = 50
ANCHOR_CLUSTER = 30  # слова одного названия характеристики стоят рядом

# Слова, по которым нельзя уверенно найти характеристику в тексте: встречаются в названиях многих характеристик.
GENERIC_WORDS = {
    "наличие", "количество", "тип", "вид", "общий", "общая", "общее", "объем", "размер", "максимальный",
    "максимальная", "минимальный", "минимальная", "встроенных", "встроенный", "корпус", "порт", "портов",
    "назначение", "материал", "класс", "товарный", "установленной", "установленный", "значение",
}


def _norm(s: str) -> str:
    return norm_text(s)


def _stem(word: str) -> str:
    """Грубая основа русского слова: «иглой» и «игла» → «игл», «проводниковой» → «проводнико»."""
    cut = 3 if len(word) >= 7 else (2 if len(word) >= 5 else 1)
    return word[: max(3, len(word) - cut)]


def _anchor_stems(name: str) -> list[str]:
    words = [w for w in re.findall(r"[а-яa-z]+", _norm(name)) if len(w) >= 4]
    specific = [w for w in words if w not in GENERIC_WORDS]
    return list(dict.fromkeys(_stem(w) for w in (specific or words)))


# Единица после запятой в названии характеристики: «…воздуха, л/мин 840», «диапазон, нм 520-546».
_UNIT_AFTER_COMMA = re.compile(r"\s*,\s*([a-zа-я/°%0-9]{1,8})(?=[\s:])")
_UNIT_WORDS = {"л/мин", "нм", "дан", "мс", "кд/м2", "g", "град", "мкм", "кпа", "па", "%", "мм", "см", "кг", "шт",
               "ч", "с", "мин", "вт", "гц", "мгц", "бит", "дб", "мл", "л", "г", "м"}


@dataclass
class QueryContext:
    text: str
    numbers: list[QueryNumber]
    _anchor_cache: dict = field(default_factory=dict)

    def anchor(self, stems: list[str]) -> Optional[tuple[int, int]]:
        """Где в тексте упомянута характеристика: (начало, конец) группы её слов.

        Берём место, где рядом стоят разные слова названия (для каждого слова — ближайшее вхождение):
        «Проводниковая игла: нет» — это характеристика, а «Игла спинальная» в начале — название товара.
        Если в названии больше одного значимого слова, одного совпадения мало: «мягкого сиденья»
        не должно считаться упоминанием «мягкой спинки».
        """
        key = tuple(stems)
        if key not in self._anchor_cache:
            hits: dict[str, list[tuple[int, int]]] = {}
            for st in stems:
                for m in re.finditer(rf"(?<![а-яa-z]){re.escape(st)}[а-яa-z]*", self.text):
                    hits.setdefault(st, []).append((m.start(), m.end()))
            need = min(2, len(stems))
            best = None
            for st, occ in hits.items():
                for s, e in occ:
                    cluster = [(s, e)]
                    for other, other_occ in hits.items():
                        if other == st:
                            continue
                        near = min(other_occ, key=lambda h: abs(h[0] - s))
                        if abs(near[0] - s) <= ANCHOR_CLUSTER:
                            cluster.append(near)
                    if len(cluster) < need:
                        continue
                    score = (len(cluster), len(st), -s)
                    if best is None or score > best[0]:
                        best = (score, min(h[0] for h in cluster), max(h[1] for h in cluster))
            if best:
                start, end = best[1], best[2]
                m = _UNIT_AFTER_COMMA.match(self.text, end)
                if m and (m.group(1) in _UNIT_WORDS or "/" in m.group(1)):
                    end = m.end()
                self._anchor_cache[key] = (start, end)
            else:
                self._anchor_cache[key] = None
        return self._anchor_cache[key]


def build_query_context(text: str) -> QueryContext:
    t = _norm(text)
    return QueryContext(text=t, numbers=find_numbers(t))


@dataclass
class EntryMatch:
    matches: list[CharacteristicMatch]
    score: float  # 0..1, 0.5 — ничего не известно
    ratio: float  # доля совпавших обязательных характеристик
    required_missing: bool
    qualities: dict[str, float]


class CharMatcher:
    def __init__(self, catalog: Catalog):
        self.vocab = {name: values for name, values in catalog.text_vocab.items()}
        self._stems: dict[str, list[str]] = {}
        self._patterns: dict[str, re.Pattern] = {}

    def _stems_for(self, char: CatalogChar) -> list[str]:
        if char.name not in self._stems:
            self._stems[char.name] = _anchor_stems(char.name)
        return self._stems[char.name]

    def _contains(self, text: str, value_norm: str) -> bool:
        pat = self._patterns.get(value_norm)
        if pat is None:
            pat = self._patterns[value_norm] = re.compile(rf"(?<![\w]){re.escape(value_norm)}(?![\w])")
        return bool(pat.search(text))

    def match_char(self, char: CatalogChar, q: QueryContext) -> tuple[str, Optional[str], float]:
        """→ (status, что найдено в тексте, качество совпадения 0..1)."""
        anchor = q.anchor(self._stems_for(char))
        ranges = [c for c in char.constraints if isinstance(c, Range)]
        texts = [c for c in char.constraints if isinstance(c, Text)]

        if ranges:
            nums = []
            if anchor:
                limit = anchor[1] + ANCHOR_WINDOW
                stop = re.search(r"[;,]", q.text[anchor[1]:limit])
                if stop:
                    limit = anchor[1] + stop.start()
                nums = [n for n in q.numbers if anchor[1] <= n.start < limit and n.unit in (None, char.unit_key)]
            if not nums and char.unit_key:
                nums = [n for n in q.numbers if n.unit == char.unit_key]
            if nums:
                best = None
                for n in nums:
                    for r in ranges:
                        if n.interval is not None:
                            qual = interval_match(n.interval, r)
                        else:
                            qual = r.quality(n.value) if r.contains(n.value) else None
                        if qual is not None and (best is None or qual > best[1]):
                            best = (n, qual)
                if best:
                    return "match", _span(q, best[0]), best[1]
                return "mismatch", _span(q, nums[0]), 0.0

        if texts:
            # Чем шире список допустимых значений, тем слабее совпадение говорит именно об этой позиции.
            breadth = 1.0 if len(texts) == 1 else max(0.6, 1.0 - 0.08 * (len(texts) - 1))
            yes_no = {t.norm for t in texts} & {"да", "нет"}
            if yes_no:
                if anchor:
                    before = q.text[max(0, anchor[0] - 5):anchor[0]]
                    after = q.text[anchor[1]:anchor[1] + 8]
                    said = "нет" if ("без" in before or re.match(r"\s*:?\s*нет", after)) else "да"
                    return ("match" if said in yes_no else "mismatch"), said.capitalize(), breadth
                return "missing", None, 0.0
            for t in texts:
                if self._contains(q.text, t.norm):
                    return "match", t.raw, breadth
            # В тексте значение этой характеристики, которого нет у позиции: другой формат, другой тип.
            own = {t.norm for t in texts}
            for other in self.vocab.get(char.name, ()):
                if other not in own and len(other) >= 2 and self._contains(q.text, other):
                    return "mismatch", other, 0.0
        return "missing", None, 0.0

    def match_entry(self, entry: CatalogEntry, q: QueryContext, required_only: bool = False) -> EntryMatch:
        matches, qualities = [], {}
        num = den = 0.0
        n_req = req_ok = 0
        req_missing = False
        for c in entry.chars:
            if required_only and not c.required:
                continue
            status, found, qual = self.match_char(c, q)
            w = W_REQUIRED if c.required else W_OPTIONAL
            if c.required:
                n_req += 1
                req_ok += status == "match"
                req_missing |= status == "missing"
            # Не указанная в тексте характеристика не голосует ни за, ни против: внутри группы она одинакова
            # у всех соседей, а между группами наказывала бы позиции с большим числом обязательных полей
            # (монитор с тремя обязательными проигрывал моноблоку с одной). Её отсутствие — флаг required_missing.
            if status != "missing":
                den += w
            if status == "match":
                num += w * qual
            elif status == "mismatch":
                num -= w
            if status != "missing" or c.required:
                matches.append(
                    CharacteristicMatch(
                        name=c.name,
                        expected=_expected(c),
                        found=found,
                        status=status,
                        required=c.required,
                    )
                )
            qualities[c.name] = qual
        score = 0.5 + 0.5 * num / (den + EVIDENCE_PRIOR) if den else 0.5
        return EntryMatch(matches, score, (req_ok / n_req) if n_req else 0.0, req_missing, qualities)


def _span(q: QueryContext, n: QueryNumber) -> str:
    """Число с единицей так, как оно написано в тексте позиции."""
    return q.text[n.start:n.end].strip()


def _expected(c: CatalogChar) -> str:
    vals = c.values[:4]
    text = "; ".join(vals) + ("; …" if len(c.values) > 4 else "")
    if c.unit and c.unit not in ("-", "шт"):
        text += f" ({c.unit})"
    return text
