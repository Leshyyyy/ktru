# Работа с документацией

Документация собирается Sphinx из Markdown (MyST). Контракты лежат в `contracts/` и подключаются в документацию напрямую,
поэтому правка контракта сразу видна в собранных страницах.

```bash
pip install -r requirements-docs.txt
sphinx-build -W -b html docs docs/_build/html
```

Флаг `-W` превращает предупреждения в ошибки: сломанная ссылка или таблица не пройдёт проверку.

## Правила

* Контракт (`openapi.yaml`, `schema.sql`, `ml_contracts.py`) важнее прозы. Расхождение — повод исправить текст.
* Изменения `ml_contracts.py` — отдельным PR с одобрением бэкенда и ML.
* Проверка контрактов: валидатор OpenAPI, примеры из `openapi.yaml` против схем, применение `schema.sql` на Postgres 16 с pgvector,
  прогон `stub_pipeline.py` на 1000 позиций.
