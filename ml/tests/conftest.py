import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from ml.catalog import Catalog, entry_from_row, load_jsonl  # noqa: E402
from ml.calibrate import HeuristicCalibrator  # noqa: E402
from ml.config import FIXTURE_CATALOG, Settings  # noqa: E402
from ml.pipeline import KtruPipeline  # noqa: E402
from ml.rerank import NoneReranker  # noqa: E402

# Искусственные позиции (не из реального каталога: его нельзя класть в публичный репозиторий).
SYNTH_ROWS = [
    {"ktru_code": "77.77.01.000-00000016", "okpd2_code": "77.77.01.000", "name": "Ноутбук", "status": "active",
     "characteristics": [{"name": "Диагональ экрана", "values": ["≥ 16"], "required": True, "unit": "Дюйм (25,4 мм)"},
                         {"name": "Тип накопителя", "values": ["SSD", "HDD"], "required": False, "unit": "-"}]},
    {"ktru_code": "77.77.01.000-00000017", "okpd2_code": "77.77.01.000", "name": "Ноутбук", "status": "active",
     "characteristics": [{"name": "Диагональ экрана", "values": ["≥ 17"], "required": True, "unit": "Дюйм (25,4 мм)"},
                         {"name": "Тип накопителя", "values": ["SSD", "HDD"], "required": False, "unit": "-"}]},
    {"ktru_code": "77.77.02.000-00000003", "okpd2_code": "77.77.02.000", "name": "Шприц одноразовый", "status": "active",
     "characteristics": [{"name": "Объем", "values": ["> 2.5 и ≤ 3"], "required": True, "unit": "см^3; мл"}]},
    {"ktru_code": "77.77.02.000-00000005", "okpd2_code": "77.77.02.000", "name": "Шприц одноразовый", "status": "active",
     "characteristics": [{"name": "Объем", "values": ["> 3 и ≤ 5"], "required": True, "unit": "см^3; мл"}]},
    {"ktru_code": "77.77.03.000-00000004", "okpd2_code": "77.77.03.000", "name": "Бумага для офисной техники",
     "status": "active",
     "characteristics": [{"name": "Формат", "values": ["А4"], "required": True, "unit": "-"}]},
    {"ktru_code": "77.77.03.000-00000003", "okpd2_code": "77.77.03.000", "name": "Бумага для офисной техники",
     "status": "active",
     "characteristics": [{"name": "Формат", "values": ["А3"], "required": True, "unit": "-"}]},
    {"ktru_code": "77.77.04.000-00000001", "okpd2_code": "77.77.04.000", "name": "Игла спинальная", "status": "active",
     "characteristics": [{"name": "Проводниковая игла", "values": ["Да"], "required": True, "unit": "-"}]},
    {"ktru_code": "77.77.04.000-00000002", "okpd2_code": "77.77.04.000", "name": "Игла спинальная", "status": "active",
     "characteristics": [{"name": "Проводниковая игла", "values": ["Нет"], "required": True, "unit": "-"}]},
    {"ktru_code": "77.77.05.000-00000001", "okpd2_code": "77.77.05.000", "name": "Монитор", "status": "archived",
     "characteristics": []},
    {"ktru_code": "77.77.05.000-00000099", "okpd2_code": "77.77.05.000", "name": "Монитор", "status": "active",
     "is_enlarged": True, "characteristics": []},
]


def make_pipeline(entries) -> KtruPipeline:
    return KtruPipeline(Settings(), catalog=Catalog(entries), calibrator=HeuristicCalibrator(), reranker=NoneReranker())


@pytest.fixture(scope="session")
def fixture_pipeline() -> KtruPipeline:
    return make_pipeline(load_jsonl(FIXTURE_CATALOG))


@pytest.fixture(scope="session")
def synth_pipeline() -> KtruPipeline:
    return make_pipeline([entry_from_row(r) for r in SYNTH_ROWS] + load_jsonl(FIXTURE_CATALOG))
