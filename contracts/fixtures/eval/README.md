# Разметка для оценки качества (`POST /quality/run`)

`migrate` загружает `*.jsonl` этой папки в `eval_samples` при каждом `docker compose up` (поля — `EvalSampleRow` в
`backend/core/quality.py`; лишние поля, например `tier`, `n_alt`, `okpd2_code`, игнорируются).

- `eval_samples_v32_dev.jsonl` — 700 строк dev, `eval_samples_v32_test.jsonl` — 300 строк test (Егор, v3.2: тексты
  синтетические, по характеристикам каталога КТРУ организаторов). Описание — `EVAL_SAMPLES_v3.2.md` у Егора.
- Из test убраны 3 строки `tier = obsolete`: их ожидаемые коды архивные, ответом системы они быть не могут.
- test — контрольный набор: запускать один раз перед сдачей, ничего под него не подбирать (правило `ml/CLAUDE.md`).
