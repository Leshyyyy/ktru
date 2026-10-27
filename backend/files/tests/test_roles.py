"""Тесты определения ролей колонок (suggest_roles)."""

from backend.files.roles import suggest_roles
from backend.files.schemas import ColumnRole


def test_suggest_roles_standard_spec():
    headers = ["№ п/п", "Наименование товара", "Характеристики", "Кол-во", "Ед. изм.", "Цена"]
    samples = [
        ["1", "2", "3"],
        ["Ноутбук Lenovo IdeaPad", "Принтер Xerox", "Монитор 24 дюйма"],
        ["15.6\", Core i5, 16 ГБ", "A4, 34 стр/мин", "IPS, 1920x1080"],
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
