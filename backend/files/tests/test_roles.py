"""Тесты определения ролей колонок (suggest_roles) и уверенности маппинга (is_mapping_confident)."""

from pathlib import Path
from backend.files.reader import read_file
from backend.files.roles import is_mapping_confident, suggest_roles
from backend.files.schemas import ColumnRole


def test_suggest_roles_standard_spec():
    headers = ["№ п/п", "Наименование товара", "Характеристики", "Кол-во", "Ед. изм.", "Цена"]
    samples = [
        ["1", "2", "3"],
        ["Ноутбук Lenovo IdeaPad", "Принтер Xerox", "Монитор 24 дюйма"],
        ['15.6", Core i5, 16 ГБ', "A4, 34 стр/мин", "IPS, 1920x1080"],
        ["10", "2", "5"],
        ["шт", "шт", "шт"],
        ["55000", "24000", "15000"],
    ]

    roles = suggest_roles(headers, samples)
    assert roles == [
        ColumnRole.IGNORE,
        ColumnRole.NAME,
        ColumnRole.CHARACTERISTICS,
        ColumnRole.QUANTITY,
        ColumnRole.UNIT,
        ColumnRole.IGNORE,
    ]


def test_suggest_roles_multiple_name_candidates():
    # Если в файле две колонки похожи на наименование, только одна должна стать name, вторая - description
    headers = ["Товар", "Наименование продукции", "Количество"]
    samples = [
        ["Бумага А4", "Картридж HP"],
        ["Бумага офисная белая", "Картридж лазерный черный"],
        ["50", "10"],
    ]
    roles = suggest_roles(headers, samples)
    assert roles.count(ColumnRole.NAME) == 1
    assert roles[0] == ColumnRole.NAME
    assert roles[1] == ColumnRole.DESCRIPTION
    assert roles[2] == ColumnRole.QUANTITY


def test_suggest_roles_no_name_header_heuristic():
    # Если заголовок не распознан, но по содержимому видно наименование
    headers = ["ColA", "ColB", "ColC"]
    samples = [
        ["Бумага офисная SvetoCopy А4 80г/м2", "Мышь беспроводная Logitech"],
        ["100", "15"],
        ["пачка", "шт"],
    ]
    roles = suggest_roles(headers, samples)
    assert roles.count(ColumnRole.NAME) == 1
    assert roles[0] == ColumnRole.NAME
    assert roles[1] == ColumnRole.QUANTITY
    assert roles[2] == ColumnRole.UNIT


def test_mapping_confident_standard_spec():
    headers = ["№ п/п", "Наименование товара", "Характеристики", "Кол-во", "Ед. изм.", "Цена"]
    assert is_mapping_confident(headers) is True


def test_mapping_not_confident_multiple_names():
    headers = ["Товар", "Наименование продукции", "Количество"]
    assert is_mapping_confident(headers) is False


def test_mapping_not_confident_no_name_header():
    headers = ["ColA", "ColB", "ColC"]
    assert is_mapping_confident(headers) is False


def test_mapping_not_confident_conflicting_quantity():
    headers = ["Наименование", "Кол-во", "Объем"]
    assert is_mapping_confident(headers) is False


def test_mapping_not_confident_conflicting_units():
    headers = ["Наименование", "Ед. изм.", "Базовая единица"]
    assert is_mapping_confident(headers) is False


def test_mapping_confident_empty_headers():
    assert is_mapping_confident([]) is False


def test_sheet_mapping_confident_on_real_spec():
    spec_path = Path(__file__).resolve().parent.parent.parent.parent / "tests" / "fixtures" / "specification_100.xlsx"
    if spec_path.exists():
        info = read_file(spec_path)
        assert len(info.sheets) > 0
        # Стандартная спецификация имеет четкие заголовки
        assert info.sheets[0].mapping_confident is True
