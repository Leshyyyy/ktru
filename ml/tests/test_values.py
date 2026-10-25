from ml.values import Range, Text, find_numbers, norm_text, parse_constraint


def test_parse_ranges():
    assert parse_constraint("≥ 17") == Range(lo=17.0)
    assert parse_constraint("> 2.5 и ≤ 3") == Range(2.5, False, 3.0, True)
    assert parse_constraint("< 0.3") == Range(hi=0.3, hi_inc=False)
    assert parse_constraint("от 8 ГБ") == Range(lo=8.0)
    assert parse_constraint('от 13"') == Range(lo=13.0)
    assert parse_constraint("65") == Range(65.0, True, 65.0, True)


def test_parse_text_values():
    assert isinstance(parse_constraint("1280 x 1024"), Text)
    assert isinstance(parse_constraint("Full HD"), Text)
    assert isinstance(parse_constraint("≥20 000 000:1"), Text)
    assert parse_constraint("А4").norm == "a4"  # кириллическая «А» приводится к латинской


def test_range_quality_prefers_tighter_bound():
    v = 17.3
    assert Range(lo=17).quality(v) > Range(lo=16).quality(v)
    assert not Range(2.5, False, 3, True).contains(2.5)


def test_find_numbers_units_and_model_tails():
    nums = find_numbers(norm_text('ноутбук lenovo m6700dw, kd-178, 15.6", ssd 512 гб, шприц 3 мл, игла 22g'))
    got = {(n.value, n.unit) for n in nums}
    assert (15.6, "inch") in got and (512.0, "gb") in got and (3.0, "ml") in got and (22.0, "gauge") in got
    assert all(n.value not in (6700.0, 178.0) for n in nums)  # хвосты моделей — не числа


def test_query_intervals():
    from ml.values import interval_match

    by_text = {n.interval: n.unit for n in find_numbers(norm_text("длина 14-15 см, более 18, не более 120, от 12 до 13 мм"))}
    assert by_text[Range(14.0, True, 15.0, True)] == "cm"
    assert Range(lo=18.0, lo_inc=False) in by_text
    assert Range(hi=120.0, hi_inc=True) in by_text
    assert by_text[Range(12.0, True, 13.0, True)] == "mm"
    q = Range(14.0, True, 15.0, True)
    assert interval_match(q, Range(14, False, 15, True)) > interval_match(q, Range(13, False, 14, True))
    assert interval_match(Range(lo=18, lo_inc=False), Range(18, True, 18, True)) is None  # «более 18» ≠ «18»
    assert interval_match(Range(hi=4, hi_inc=False), Range(4, True, 6, False)) is None  # «менее 4» ≠ «≥ 4 и < 6»


def test_ktru_full_unit_names():
    from ml.values import unit_key_from_ktru

    assert unit_key_from_ktru("Кубический сантиметр") == "ml"
    assert unit_key_from_ktru("Сантиметр") == "cm"
    assert unit_key_from_ktru("Квадратный метр") is None
    assert unit_key_from_ktru("Градус Цельсия") == "c"
    assert unit_key_from_ktru("Килограмм") == "kg"
    assert unit_key_from_ktru("Месяц") == "month"
    nums = find_numbers(norm_text("объем 5 кубический сантиметр, вес 2 килограмма, температура 40 градусов цельсия"))
    assert {(n.value, n.unit) for n in nums} >= {(5.0, "ml"), (2.0, "kg"), (40.0, "c")}
