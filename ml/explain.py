"""Пояснения к предсказаниям. Называем только характеристики из characteristic_matches —
так требует контракт (Prediction.explanation): объяснение не должно ссылаться на то, чего нет в данных."""

from __future__ import annotations

from .charmatch import EntryMatch


def _lc(name: str) -> str:
    return name[:1].lower() + name[1:]


def explanation(m: EntryMatch) -> str:
    matched = [x.name for x in m.matches if x.status == "match"]
    mismatched = [x for x in m.matches if x.status == "mismatch"]
    missing_req = [x.name for x in m.matches if x.required and x.status == "missing"]
    parts = []
    if matched:
        parts.append("Совпадает: " + ", ".join(_lc(n) for n in matched[:4]) + ".")
    if mismatched:
        x = mismatched[0]
        parts.append(f"Не совпадает {_lc(x.name)}: в тексте {x.found}, по КТРУ {x.expected}.")
    if missing_req:
        parts.append("Не указано в тексте, но обязательно по КТРУ: " + ", ".join(_lc(n) for n in missing_req[:3]) + ".")
    if not parts:
        return "Код выбран по наименованию: характеристик для сверки в тексте нет."
    return " ".join(parts)


def why_not_chosen(best: EntryMatch, other: EntryMatch, best_name_score: float, other_name_score: float) -> str:
    for x in other.matches:
        if x.status == "mismatch":
            return f"Не совпадает {_lc(x.name)}: в тексте {x.found}, по КТРУ {x.expected}."
    for name, q in other.qualities.items():
        if name in best.qualities and q + 0.01 < best.qualities[name] and q > 0:
            return f"Характеристика «{name}» подходит, но условие КТРУ шире, чем у первого варианта."
    for x in other.matches:
        if x.required and x.status == "missing" and best.qualities.get(x.name, 0) == 0:
            return f"Обязательная характеристика «{x.name}» в тексте не найдена."
    if other_name_score + 0.05 < best_name_score:
        return "Наименование позиции КТРУ хуже совпадает с текстом."
    return "Ниже общая оценка совпадения с текстом позиции."
