"""Определение и предложение ролей колонок (suggest_roles) по заголовкам и содержимому."""

import re
from typing import List, Optional
from backend.files.schemas import ColumnRole

# Словари заголовков
_ROLE_PATTERNS = {
    ColumnRole.NAME: [
        r"наименовани[ея]",
        r"товар[аы]?",
        r"позици[яи]",
        r"номенклатур[аы]",
        r"предмет\s+закупк[иа]",
        r"названи[ея]",
        r"продукци[яи]",
    ],
    ColumnRole.DESCRIPTION: [
        r"описани[ея]",
        r"техническ(?:ие|ое)\s+требовани[ея]",
        r"тех\.?\s*описани[ея]",
        r"описани[ея]\s+объекта",
    ],
    ColumnRole.CHARACTERISTICS: [
        r"характеристик[аи]",
        r"параметр[ыа]",
        r"^тх$",
        r"тех\.?\s*характеристик[аи]",
        r"спецификаци[яи]",
        r"требования\s+к\s+товару",
    ],
    ColumnRole.QUANTITY: [
        r"^кол[-.]?во",
        r"^количеств[оа]",
        r"^объем",
        r"^объём",
        r"^кол$",
    ],
    ColumnRole.UNIT: [
        r"^ед\.?\s*изм\.?",
        r"^единиц[аы]",
        r"^базовая\s+единица",
        r"^окпу$",
        r"^ед$",
    ],
    ColumnRole.IGNORE: [
        r"^№",
        r"^п/?п",
        r"^код",
        r"окпд",
        r"цен[аы]",
        r"стоимост",
        r"сумм[аы]",
        r"ндс",
        r"производител",
        r"стран[аы]",
        r"артикул",
        r"примечани",
    ],
}

_UNIT_SAMPLES = {
    "шт",
    "шт.",
    "штук",
    "кг",
    "килограмм",
    "м",
    "метр",
    "пог. м",
    "компл",
    "компл.",
    "комплект",
    "упак",
    "упак.",
    "упаковка",
    "л",
    "литр",
    "пар",
    "пара",
    "усл. ед",
    "набор",
    "рулон",
    "пачка",
}


def _match_header_pattern(header: str, role: ColumnRole) -> bool:
    h_lower = header.strip().lower()
    for pat in _ROLE_PATTERNS.get(role, []):
        if re.search(pat, h_lower):
            return True
    return False


def _heuristic_from_samples(samples: List[str]) -> Optional[ColumnRole]:
    """Эвристика роли по содержимому ячеек."""
    clean_samples = [s.strip() for s in samples if s and s.strip()]
    if not clean_samples:
        return None

    # Проверка на количество (все числа)
    numeric_count = 0
    for s in clean_samples:
        # Проверяем, число ли это (например, 10, 10.5, 1 000)
        num_str = s.replace(" ", "").replace(",", ".")
        try:
            float(num_str)
            numeric_count += 1
        except ValueError:
            pass

    if numeric_count / len(clean_samples) >= 0.8:
        return ColumnRole.QUANTITY

    # Проверка на единицы измерения
    unit_count = sum(1 for s in clean_samples if s.lower() in _UNIT_SAMPLES)
    if unit_count / len(clean_samples) >= 0.6:
        return ColumnRole.UNIT

    # Длинный текст с техническими параметрами
    avg_len = sum(len(s) for s in clean_samples) / len(clean_samples)
    if avg_len > 60:
        return ColumnRole.CHARACTERISTICS

    return None


def suggest_roles(headers: List[str], samples_by_col: List[List[str]]) -> List[ColumnRole]:
    """
    Определяет предполагаемые роли для каждой колонки.
    Гарантирует правила бизнес-логики:
    - Ровно одна колонка с ролью 'name'.
    - Не более одной колонки с ролью 'quantity'.
    - Не более одной колонки с ролью 'unit'.
    """
    roles: List[Optional[ColumnRole]] = [None] * len(headers)

    # Шаг 1: Точное или словарное сопоставление по заголовкам
    for i, header in enumerate(headers):
        for role in [
            ColumnRole.IGNORE,
            ColumnRole.NAME,
            ColumnRole.QUANTITY,
            ColumnRole.UNIT,
            ColumnRole.CHARACTERISTICS,
            ColumnRole.DESCRIPTION,
        ]:
            if _match_header_pattern(header, role):
                roles[i] = role
                break

    # Шаг 2: Эвристика по содержимому для неопределённых колонок
    for i in range(len(headers)):
        if roles[i] is None:
            samples = samples_by_col[i] if i < len(samples_by_col) else []
            heur = _heuristic_from_samples(samples)
            if heur:
                roles[i] = heur
            else:
                roles[i] = ColumnRole.IGNORE

    # Шаг 3: Нормализация ограничений бизнес-логики
    # Ограничение: максимум одна quantity
    quantity_indices = [i for i, r in enumerate(roles) if r == ColumnRole.QUANTITY]
    if len(quantity_indices) > 1:
        # Оставляем первую, остальные помечаем ignore
        for idx in quantity_indices[1:]:
            roles[idx] = ColumnRole.IGNORE

    # Ограничение: максимум одна unit
    unit_indices = [i for i, r in enumerate(roles) if r == ColumnRole.UNIT]
    if len(unit_indices) > 1:
        for idx in unit_indices[1:]:
            roles[idx] = ColumnRole.IGNORE

    # Ограничение: ровно одна колонка name
    name_indices = [i for i, r in enumerate(roles) if r == ColumnRole.NAME]
    if len(name_indices) == 1:
        pass
    elif len(name_indices) > 1:
        # Если найдено несколько name, первую оставляем name, остальные делаем description
        for idx in name_indices[1:]:
            roles[idx] = ColumnRole.DESCRIPTION
    else:
        # Не найдено ни одного name! Выбираем лучшего кандидата среди non-quantity/non-unit колонок
        candidates = []
        for i in range(len(headers)):
            if roles[i] not in (ColumnRole.QUANTITY, ColumnRole.UNIT):
                samples = samples_by_col[i] if i < len(samples_by_col) else []
                avg_len = sum(len(s) for s in samples) / (len(samples) or 1)
                candidates.append((i, avg_len))

        if candidates:
            # Сортируем по средней длине и выбираем умеренную колонку (наименование обычно 15-80 символов)
            candidates.sort(key=lambda x: (x[1] >= 10, -abs(x[1] - 40)), reverse=True)
            best_idx = candidates[0][0]
            roles[best_idx] = ColumnRole.NAME
        elif roles:
            # Если вообще все были quantity/unit (крайне редкий случай)
            roles[0] = ColumnRole.NAME

    # Гарантируем, что нет None
    return [r or ColumnRole.IGNORE for r in roles]
