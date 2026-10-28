from conftest import SYNTH_ROWS, make_pipeline

from contracts.ml_contracts import PipelineOptions, RawItem
from ml.catalog import Catalog, CatalogEntry, entry_from_row
from ml.category import CategoryModel, query_text
from ml.charmatch import CharMatcher, build_query_context

# Искусственные «карточки поставщиков» с кодами ОКПД2 из SYNTH_ROWS.
NAMES = [
    ("Ноутбук Lenovo IdeaPad 15", "77.77.01.000"),
    ("Ноутбук ASUS VivoBook", "77.77.01.000"),
    ("Ноутбук HP ProBook 450", "77.77.01.000"),
    ("Шприц 3 мл одноразовый", "77.77.02.000"),
    ("Шприц инъекционный 5 мл", "77.77.02.000"),
    ("Бумага офисная А4 SvetoCopy", "77.77.03.000"),
    ("Бумага для принтера А4 500 листов", "77.77.03.000"),
]


class FixedCategories:
    """Модель категорий, которая всегда отвечает одно и то же."""

    def __init__(self, probs):
        self.probs = probs

    def predict_batch(self, texts):
        return [(dict(self.probs), 1.0) for _ in texts]


def _run(pipe, text, **columns):
    return pipe.classify_batch([RawItem(item_id="1", text=text, columns=columns)], "",
                               PipelineOptions(use_feedback=False))[0]


def test_category_model_votes_by_neighbours():
    model = CategoryModel.fit([n for n, _ in NAMES], [c for _, c in NAMES])
    (probs, sim), (probs2, _) = model.predict_batch(["ноутбук lenovo thinkpad", "бумага А4 для принтера"], k=3)
    assert next(iter(probs)) == "77.77.01.000" and sim > 0
    assert abs(sum(probs.values()) - 1) < 1e-6
    assert next(iter(probs2)) == "77.77.03.000"


def test_query_text_is_name_without_characteristics():
    assert query_text("Ноутбук Lenovo; Диагональ: 15,6 дюйм") == "Ноутбук Lenovo"
    assert query_text("Ноутбук\nДиагональ 15,6") == "Ноутбук"
    assert query_text("склейка всех колонок", name_column="Ноутбук") == "Ноутбук"


def test_candidates_come_from_predicted_category():
    # Текст одинаково похож на бумагу и на шприцы; модель категорий говорит «бумага» — ответ из бумаги,
    # и внутри категории сверка характеристик выбирает формат.
    pipe = make_pipeline([entry_from_row(r) for r in SYNTH_ROWS], FixedCategories({"77.77.03.000": 0.9,
                                                                                    "77.77.02.000": 0.1}))
    r = _run(pipe, "Изделие, формат А3")
    assert r.predictions[0].ktru_code == "77.77.03.000-00000003"
    assert r.timings_ms["category"] >= 0


def test_unknown_categories_fall_back_to_whole_catalog():
    pipe = make_pipeline([entry_from_row(r) for r in SYNTH_ROWS], FixedCategories({"99.99.99.999": 1.0}))
    r = _run(pipe, "Шприц одноразовый, объем 5 мл")
    assert r.predictions[0].ktru_code == "77.77.02.000-00000005"


def test_text_search_still_reaches_codes_outside_categories():
    # Модель категорий ошиблась (ноутбуки), но запасные кандидаты из всего каталога остаются в выдаче.
    pipe = make_pipeline([entry_from_row(r) for r in SYNTH_ROWS], FixedCategories({"77.77.01.000": 1.0}))
    r = _run(pipe, "Шприц одноразовый, объем 5 мл")
    assert "77.77.02.000-00000005" in [p.ktru_code for p in r.predictions]


def test_yes_from_bare_mention_is_weak_evidence():
    entry: CatalogEntry = entry_from_row({
        "ktru_code": "77.77.09.000-00000001", "okpd2_code": "77.77.09.000", "name": "Стерилизатор", "status": "active",
        "characteristics": [{"name": "Принтер", "values": ["Да"], "required": True, "unit": "-"}]})
    matcher = CharMatcher(Catalog([entry]))
    char = entry.chars[0]
    assert matcher.match_char(char, build_query_context("Принтер лазерный цветной А4"))[3] is True
    assert matcher.match_char(char, build_query_context("Стерилизатор, принтер: да"))[3] is False
    weak = matcher.match_entry(entry, build_query_context("Принтер лазерный цветной А4")).score
    strong = matcher.match_entry(entry, build_query_context("Стерилизатор, принтер: да")).score
    assert 0.5 < weak < strong

