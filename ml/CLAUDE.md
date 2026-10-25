# ml/ — подбор кода КТРУ

Владелец — Jove. Бэкенд вызывает только `ml.pipeline.get_pipeline().classify_batch(...)`; интерфейс — `contracts/ml_contracts.py`, его не меняем без PR с одобрением бэкенда.

## Как устроено

| Файл | Что делает |
| --- | --- |
| `pipeline.py` | `KtruPipeline` (реализация `Pipeline`): атрибуты → кандидаты → сверка характеристик → реранкер для неуверенных → калибровка → топ-3 с пояснениями |
| `extract.py` | `RulesExtractor`: наименование, бренд (словарь + эвристика), модель, характеристики «ключ: значение» |
| `values.py` | Разбор значений КТРУ («≥ 17», «> 2.5 и ≤ 3», «от 8 ГБ») и чисел с единицами из текста позиции |
| `charmatch.py` | Сверка характеристик: match / mismatch / missing, привязка к названию характеристики в тексте |
| `retrieve.py` | `MemoryRetriever`: TF-IDF (или эмбеддер) + нечёткое название + обязательные характеристики; кэш подтверждённых примеров |
| `rerank.py` | none / cross_encoder / llm (Jev через OpenRouter) |
| `calibrate.py` | Калибратор уверенности; `python -m ml.calibrate` обучает на dev |
| `evaluate.py` | Метрики раздела 9 аналитики |
| `synth.py` | Синтетический dev по характеристикам — временная замена ручной разметке |

Главное о данных: внутри одного кода ОКПД2 позиции КТРУ называются одинаково и различаются обязательными числовыми характеристиками. Поэтому качество решает `charmatch.py`, а не поиск по тексту.

## Команды (из корня репозитория)

```bash
python -m pytest ml/tests -q
python -m ml.synth --candidates data/ktru/ktru_data/eval_dev_candidates.jsonl --out data/ktru/eval/dev_synth.jsonl
python -m ml.calibrate --data data/ktru/eval/dev_synth.jsonl
python -m ml.evaluate --data data/ktru/eval/dev_synth.jsonl --split dev
```

## Правила

- Реальный каталог (`data/`) не коммитить и не класть в тесты: условия moy-zakupki.ru (см. KTRU_DATA.md у Егора). В тестах — только `contracts/fixtures/` и искусственные строки из `ml/tests/conftest.py`.
- `eval_test_candidates` и test-разметку не открывать при разработке; пороги и веса — только по dev.
- `ml/` не импортирует FastAPI и Celery.
- Синтетические метрики оптимистичны — в отчёты и видео идут только цифры на ручной разметке.
- Новая логика — с тестом в `ml/tests/`.
