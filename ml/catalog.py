"""Каталог КТРУ в памяти: загрузка JSONL, текст для поиска, разобранные характеристики."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

from contracts.ml_contracts import CatalogRow

from .values import Constraint, parse_constraint, unit_key_from_ktru


@dataclass
class CatalogChar:
    name: str
    values: list[str]
    required: bool
    unit: Optional[str]
    unit_key: Optional[str]
    constraints: list[Constraint]


@dataclass
class CatalogEntry:
    ktru_code: str
    okpd2_code: str
    name: str
    status: str
    is_enlarged: bool
    unit: Optional[str]
    chars: list[CatalogChar]
    raw_characteristics: list[dict] = field(default_factory=list)

    @property
    def searchable(self) -> bool:
        """Может быть ответом: действующая и не укрупнённая позиция."""
        return self.status == "active" and not self.is_enlarged

    def search_text(self) -> str:
        """Что индексируется: наименование, названия характеристик и текстовые значения (без чисел)."""
        parts = [self.name]
        for c in self.chars:
            parts.append(c.name)
            for v in c.values:
                if not any(ch.isdigit() for ch in v) and v not in ("Да", "Нет"):
                    parts.append(v)
        return " ".join(parts)


def entry_from_row(row: dict) -> CatalogEntry:
    # Поля сверх CatalogRow (is_enlarged, unit и kind у характеристик) приходят из выгрузки moy-zakupki.
    base = CatalogRow.model_validate(row)
    chars = []
    for c in base.characteristics:
        values = [str(v) for v in c.get("values", [])]
        chars.append(
            CatalogChar(
                name=c["name"],
                values=values,
                required=bool(c.get("required")),
                unit=c.get("unit"),
                unit_key=unit_key_from_ktru(c.get("unit"), c["name"]),
                constraints=[parse_constraint(v) for v in values],
            )
        )
    return CatalogEntry(
        ktru_code=base.ktru_code,
        okpd2_code=base.okpd2_code,
        name=base.name,
        status=base.status,
        is_enlarged=bool(row.get("is_enlarged", False)),
        unit=base.unit,
        chars=chars,
        raw_characteristics=base.characteristics,
    )


def load_jsonl(path: str | Path) -> list[CatalogEntry]:
    with open(path, encoding="utf-8") as f:
        return [entry_from_row(json.loads(line)) for line in f if line.strip()]


class Catalog:
    def __init__(self, entries: Iterable[CatalogEntry]):
        self.entries = list(entries)
        self.by_code = {e.ktru_code: e for e in self.entries}
        self.searchable = [e for e in self.entries if e.searchable]
        # Словарь текстовых значений по названию характеристики — по всему каталогу.
        # Нужен, чтобы отличить «в тексте другой формат (A3)» (mismatch) от «формат не указан» (missing).
        self.text_vocab: dict[str, set[str]] = {}
        for e in self.entries:
            for c in e.chars:
                vocab = self.text_vocab.setdefault(c.name, set())
                for con in c.constraints:
                    if hasattr(con, "norm") and con.norm not in ("да", "нет"):
                        vocab.add(con.norm)

    def __len__(self) -> int:
        return len(self.entries)
