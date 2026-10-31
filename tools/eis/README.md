# Инструменты сбора реальных данных ЕИС

Офлайн-утилиты для разметки, в продукт и образы Docker не входят (`.dockerignore` пропускает только `backend/`,
`contracts/` и `ml/`).

- `real_samples_tool.py` — сборка и проверка набора `real_samples.jsonl` из заполненного CSV или XML-выгрузок
  извещений ЕИС: `python -m tools.eis.real_samples_tool csv real_hunt_list.csv --catalog catalog.jsonl`.
  Тесты — `tests/test_real_samples_tool.py`.
