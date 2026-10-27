"""Тесты утилит очистки и хеширования."""

from pathlib import Path
from backend.files.clean import clean_cell, is_empty_row, is_header_repeat, is_summary_row
from backend.files.hash import compute_file_sha256, compute_headers_hash


def test_clean_cell():
    assert clean_cell("  Ноутбук   «Lenovo»  ") == 'Ноутбук "Lenovo"'
    assert clean_cell(12.0) == "12"
    assert clean_cell(12.5) == "12.5"
    assert clean_cell(None) == ""
    assert clean_cell("Спецификация\u00a0товара") == "Спецификация товара"


def test_is_empty_row():
    assert is_empty_row(["", "   ", ""]) is True
    assert is_empty_row(["", "1", ""]) is False


def test_is_summary_row():
    assert is_summary_row(["Итого", "1000", "руб."]) is True
    assert is_summary_row(["Всего по спецификации:", "", ""]) is True
    assert is_summary_row(["В том числе НДС", "", ""]) is True
    assert is_summary_row(["Ноутбук", "5", "шт"]) is False


def test_is_header_repeat():
    headers = ["№", "Наименование", "Кол-во"]
    assert is_header_repeat(["1", "Наименование", "Кол-во"], headers) is True
    assert is_header_repeat(["1", "Ноутбук", "10"], headers) is False


def test_compute_hashes(tmp_path: Path):
    f = tmp_path / "sample.txt"
    f.write_text("Test content", encoding="utf-8")
    sha = compute_file_sha256(f)
    assert len(sha) == 64

    h1 = compute_headers_hash(["Наименование", "Кол-во", "Ед. изм."])
    h2 = compute_headers_hash(["  наименование ", "кол-во  ", "ед. изм."])
    assert h1 == h2
