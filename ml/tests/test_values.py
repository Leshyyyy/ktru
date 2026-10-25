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
