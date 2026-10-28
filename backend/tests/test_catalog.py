"""Импорт каталога без базы: текст для поиска и чтение JSONL."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from backend.core import catalog
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


def test_download_catalog_is_atomic(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Файл появляется только после успешной загрузки: оборванная не оставляет «готовый» каталог."""
    path = tmp_path / "catalog" / "catalog.jsonl"

    def ok(command: list[str], **_: object) -> None:
        assert command[1:4] == ["-m", "ml.fetch_catalog", "--out"]
        Path(command[4]).write_text("{}\n", encoding="utf-8")

    def broken(command: list[str], **_: object) -> None:
        Path(command[4]).write_text("{", encoding="utf-8")
        raise subprocess.CalledProcessError(1, command)

    monkeypatch.setattr(catalog.subprocess, "run", broken)
    assert catalog.download_catalog(path) is False
    assert list(path.parent.iterdir()) == []
    monkeypatch.setattr(catalog.subprocess, "run", ok)
    assert catalog.download_catalog(path) is True
    assert [p.name for p in path.parent.iterdir()] == ["catalog.jsonl"]
