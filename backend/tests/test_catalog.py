"""Импорт каталога без базы: текст для поиска и чтение JSONL."""

from __future__ import annotations

from backend.core.catalog import build_search_text
from contracts.ml_contracts import CatalogRow


def test_build_search_text_skips_numbers_and_flags() -> None:
    row = CatalogRow(
        ktru_code="1",
        okpd2_code="1",
        name="Ноутбук",
        description="Портативный компьютер",
        characteristics=[
            {"name": "Тип матрицы", "values": ["IPS", "OLED"]},
            {"name": "Диагональ", "values": ["≥ 15.6"]},
            {"name": "Подсветка клавиатуры", "values": ["Да", "Нет"]},
        ],
    )
    assert build_search_text(row) == "Ноутбук Портативный компьютер Тип матрицы IPS OLED Диагональ Подсветка клавиатуры"
