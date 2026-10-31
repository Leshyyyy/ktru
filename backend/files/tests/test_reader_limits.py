"""read_file не читает файл целиком ради предпросмотра: больше MAX_ROWS_IN_FILE строк — отказ сразу (карточка P0-2)."""

import csv
import time
from pathlib import Path

import openpyxl
import pytest

from backend.files import read_file
from backend.files.exceptions import LimitExceededError
from backend.files.reader import MAX_ROWS_IN_FILE


def _csv(tmp_path: Path, n: int) -> Path:
    f = tmp_path / f"c{n}.csv"
    with open(f, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, delimiter=";")
        w.writerow(["Наименование", "Кол-во", "Ед."])
        for i in range(n):
            w.writerow([f"Ноутбук {i}", 1, "шт"])
    return f


def _xlsx(tmp_path: Path, n: int) -> Path:
    wb = openpyxl.Workbook(write_only=True)
    ws = wb.create_sheet("Спецификация")
    ws.append(["Наименование", "Кол-во"])
    for i in range(n):
        ws.append([f"Товар {i}", 1])
    f = tmp_path / f"x{n}.xlsx"
    wb.save(f)
    return f


def test_csv_at_limit_is_read(tmp_path: Path) -> None:
    assert read_file(_csv(tmp_path, MAX_ROWS_IN_FILE)).sheets[0].row_count == MAX_ROWS_IN_FILE


def test_csv_over_limit_fails_fast_with_limit_exceeded(tmp_path: Path) -> None:
    f = _csv(tmp_path, 300_000)
    started = time.monotonic()
    with pytest.raises(LimitExceededError) as e:
        read_file(f)
    assert e.value.code == "limit_exceeded"
    assert str(MAX_ROWS_IN_FILE) in e.value.message
    assert time.monotonic() - started < 1.0


def test_xlsx_over_limit_names_the_sheet(tmp_path: Path) -> None:
    with pytest.raises(LimitExceededError) as e:
        read_file(_xlsx(tmp_path, 20_000))
    assert "Спецификация" in e.value.message


def test_xlsx_within_limit_is_read(tmp_path: Path) -> None:
    assert read_file(_xlsx(tmp_path, 100)).sheets[0].row_count == 100
