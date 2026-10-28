from contracts.ml_contracts import CatalogRow

from ml.catalog import entry_from_row
from ml.fetch_catalog import convert_row
from ml.values import Range

# Искусственная строка в формате выгрузки ЕИС из датасета (не реальная позиция каталога).
RAW = {
    "code": "77.77.09.000-00000001",
    "name": "Изделие тестовое",
    "status": "ACTIVE",
    "is_template": False,
    "okpd2": {"code": "77.77.09.000", "name": "Тестовая группа"},
    "units": [{"code": "796", "name": "Штука"}],
    "characteristics": [
        {"name": "Длина", "required": True, "actual": True,
         "values": [{"unit": {"code": "006", "name": "Миллиметр"},
                     "ranges": [{"min": 10, "min_operator": "greaterOrEqual", "max": 20, "max_operator": "less"}]}]},
        {"name": "Цвет", "required": False, "actual": True, "values": [{"text": "Белый"}, {"text": "Белый"}]},
        {"name": "Устаревшая", "required": True, "actual": False, "values": [{"text": "x"}]},
    ],
}


def test_convert_row_to_catalog_row():
    row = convert_row(RAW)
    CatalogRow.model_validate(row)
    assert row["status"] == "active" and row["is_enlarged"] is False and row["unit"] == "Штука"
    assert [c["name"] for c in row["characteristics"]] == ["Длина", "Цвет"]  # неактуальная характеристика выкинута
    length, color = row["characteristics"]
    assert length == {"name": "Длина", "values": ["≥ 10 и < 20"], "required": True, "unit": "Миллиметр", "kind": "range"}
    assert color["values"] == ["Белый"]  # без дублей


def test_converted_row_is_understood_by_ml():
    entry = entry_from_row(convert_row(RAW))
    length = entry.chars[0]
    assert length.unit_key == "mm"
    assert length.constraints == [Range(10.0, True, 20.0, False)]
