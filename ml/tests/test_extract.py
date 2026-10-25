from contracts.ml_contracts import RawItem

from ml.extract import RulesExtractor

ex = RulesExtractor()


def _x(text, **columns):
    return ex.extract_one(RawItem(item_id="1", text=text, columns=columns))


def test_brand_and_multiword_model():
    a = _x('Ноутбук Lenovo IdeaPad 3 15IAU7, 15.6", Core i5-1235U, 16 ГБ')
    assert (a.name, a.brand, a.model) == ("Ноутбук", "Lenovo", "IdeaPad 3 15IAU7")


def test_compatible_printer_is_not_brand():
    a = _x("Картридж лазерный черный, совместим с HP LaserJet Pro M404, ресурс не менее 3000 стр.")
    assert a.name == "Картридж лазерный черный"
    assert a.brand is None and a.model is None


def test_number_after_brand_is_not_model():
    a = _x("Флеш-накопитель Kingston 64 ГБ")
    assert a.brand == "Kingston" and a.model is None


def test_name_with_lowercase_clarification():
    assert _x("Монитор, подключаемый к компьютеру, Размер диагонали 24").name == "Монитор, подключаемый к компьютеру"


def test_quantity_and_unit_from_columns():
    a = _x("Бумага офисная А4", name="Бумага офисная А4", quantity="1 200", unit="пач")
    assert a.quantity == 1200 and a.unit == "пач" and a.field_confidence["name"] >= 0.9


def test_multiword_and_cyrillic_brands():
    a = _x("Ручка канцелярская Erich Krause. вид: Шариковая; ручка автоматическая: да")
    assert (a.name, a.brand, a.model) == ("Ручка канцелярская", "Erich Krause", None)
    b = _x("Бумага для офисной техники Снегурочка. формат: А4")
    assert (b.name, b.brand) == ("Бумага для офисной техники", "Снегурочка")


def test_trailing_number_in_model_and_latin_in_name():
    assert _x("Системный блок iRU Office 313. объем оперативной памяти: не менее 8 ГБ").model == "Office 313"
    a = _x("USB-ресивер для беспроводной связи A4Tech Unifying, дальность действия не менее 10 м")
    assert (a.name, a.brand, a.model) == ("USB-ресивер для беспроводной связи", "A4Tech", "Unifying")
