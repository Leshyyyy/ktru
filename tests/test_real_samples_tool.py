"""
Тесты для утилиты сборки и валидации датасета tools/eis/real_samples_tool.py.
"""

import json
import os
import pytest
from tools.eis.real_samples_tool import DatasetBuilder, normalize_notice, process_csv


def test_normalize_notice():
    assert normalize_notice("0373100000125000123") == "извещение 0373100000125000123"
    assert normalize_notice("№ 0373100000125000123 от 2025") == "извещение 0373100000125000123"
    assert normalize_notice("извещение 0373100000125000123") == "извещение 0373100000125000123"
    assert normalize_notice("12345") is None
    assert normalize_notice("") is None


def test_dataset_builder_valid_acceptance(tmp_path):
    catalog = {"17.12.14.110-00000008": "ACTIVE"}
    builder = DatasetBuilder(catalog=catalog, catalog_source="test_catalog")

    accepted = builder.add_candidate(
        raw_text="Бумага для офисной техники белая; Формат: А4; Плотность: 80 г/м2",
        ktru_code="17.12.14.110-00000008",
        notice="0373100000125000123",
        origin_ref="test.csv:строка 2",
        area="канцелярия",
    )
    assert accepted is True
    assert len(builder.accepted) == 1
    assert builder.accepted[0]["id"] == "r-0001"
    assert builder.accepted[0]["split"] == "test"
    assert builder.accepted[0]["source"] == "извещение 0373100000125000123"
    assert builder.accepted[0]["expected_ktru_code"] == "17.12.14.110-00000008"


def test_dataset_builder_rejections():
    catalog = {
        "17.12.14.110-00000008": "ACTIVE",
        "17.12.14.110-00000009": "ARCHIVED",
    }
    builder = DatasetBuilder(catalog=catalog)

    # 1. Текст короче 15 символов
    res = builder.add_candidate("Бумага А4", "17.12.14.110-00000008", "0373100000125000123", "row 1")
    assert res is False
    assert "короткий" in builder.rejected[-1]["reason"]

    # 2. Неверный номер извещения
    res = builder.add_candidate(
        "Бумага для офисной техники белая А4 80г", "17.12.14.110-00000008", "12345", "row 2"
    )
    assert res is False
    assert "19 цифр" in builder.rejected[-1]["reason"]

    # 3. Шаблонный код КТРУ (-00000000)
    res = builder.add_candidate(
        "Бумага для офисной техники белая А4 80г", "17.12.14.110-00000000", "0373100000125000123", "row 3"
    )
    assert res is False
    assert "-00000000" in builder.rejected[-1]["reason"]

    # 4. Код в архиве
    res = builder.add_candidate(
        "Бумага для офисной техники белая А4 80г", "17.12.14.110-00000009", "0373100000125000123", "row 4"
    )
    assert res is False
    assert "устаревший/архивный" in builder.rejected[-1]["reason"]

    # 5. Код отсутствует в каталоге
    res = builder.add_candidate(
        "Бумага для офисной техники белая А4 80г", "99.99.99.999-00000001", "0373100000125000123", "row 5"
    )
    assert res is False
    assert "отсутствует в каталоге" in builder.rejected[-1]["reason"]


def test_dataset_builder_quota_and_duplicate():
    catalog = {"17.12.14.110-00000008": "ACTIVE"}
    builder = DatasetBuilder(catalog=catalog)

    # Добавляем 5 уникальных позиций для одного кода
    for i in range(1, 6):
        res = builder.add_candidate(
            f"Бумага для офисной техники вариант {i} белая А4",
            "17.12.14.110-00000008",
            f"037310000012500012{i}",
            f"row {i}",
        )
        assert res is True

    # 6-я строка с тем же кодом должна быть отклонена по лимиту квоты 5
    res = builder.add_candidate(
        "Бумага для офисной техники вариант 6 белая А4",
        "17.12.14.110-00000008",
        "0373100000125000126",
        "row 6",
    )
    assert res is False
    assert "уже набрано 5 строк" in builder.rejected[-1]["reason"]

    # Дубликат (тот же код и текст как у строки 1)
    res = builder.add_candidate(
        "Бумага для офисной техники вариант 1 белая А4",
        "17.12.14.110-00000008",
        "0373100000125000999",
        "row 7",
    )
    assert res is False
    assert "Дубликат" in builder.rejected[-1]["reason"]


def test_process_csv_integration(tmp_path):
    csv_file = tmp_path / "hunt.csv"
    csv_content = """область;ktru_code;наименование;okpd2;ссылка;notice (номер извещения);raw_text;нашёл? (да/нет)
канцелярия;17.12.14.110-00000008;Бумага;17.12;http://...;0373100000125000123;Бумага для офисной техники белая А4 80г;да
канцелярия;17.12.14.112-00000004;Бумага офсетная;17.12;http://...;;;нет
канцелярия;17.12.14.110-00000008;Бумага;17.12;http://...;0373100000125000124;Бумага для принтера плотность 80г А4;да
"""
    csv_file.write_text(csv_content, encoding="utf-8")

    catalog = {
        "17.12.14.110-00000008": "ACTIVE",
        "17.12.14.112-00000004": "ACTIVE",
    }
    builder = DatasetBuilder(catalog=catalog)
    process_csv(str(csv_file), builder)

    assert len(builder.accepted) == 2
    assert builder.skipped_empty_count == 1
    assert builder.accepted[0]["id"] == "r-0001"
    assert builder.accepted[1]["id"] == "r-0002"

    out_jsonl = tmp_path / "out.jsonl"
    report_txt = tmp_path / "report.txt"
    builder.export_results(str(out_jsonl), str(report_txt))

    assert out_jsonl.exists()
    assert report_txt.exists()

    with open(out_jsonl, "r", encoding="utf-8") as f:
        lines = [json.loads(line) for line in f]
    assert len(lines) == 2
    assert lines[0]["split"] == "test"
    assert "_area" not in lines[0]  # служебное поле исключено
