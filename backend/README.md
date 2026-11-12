# Бэкенд: проверка руками через curl

Черновик раздела для корневого README (его ведёт Егор). Команды проверены на `docker compose up` с заглушкой ML
(`PIPELINE=stub`, коды 99.99.\*). Нужны `curl` и `jq`; файл — любая спецификация xlsx, csv или pdf до 50 МБ.

```bash
docker compose up --build -d
API=http://127.0.0.1:8000/api/v1
curl -s $API/health | jq .ready          # true — модели и каталог загружены, можно создавать задачи
```

**1. Загрузить файл.** В ответе листы, предпросмотр и предложенные роли колонок. Для такого же файла придёт
`duplicate_of_job_id`, для таких же заголовков — `layout_hint` с прошлым сопоставлением.

```bash
curl -s -F "file=@spec.xlsx" $API/files | tee file.json \
  | jq '.sheets[] | {name, row_count, header_row, columns: [.columns[] | {index, header, suggested_role}]}'
FILE=$(jq -r .file_id file.json)
HEADER_ROW=$(jq -r '.sheets[0].header_row' file.json)
```

**2. Создать задачу.** Ровно одна колонка `name`, не больше одной `quantity` и `unit`; остальные — `ignore`.
Ответ 202 приходит сразу, файл разбирается в очереди.

```bash
curl -s -X POST $API/jobs -H 'Content-Type: application/json' -d '{
  "file_id": "'"$FILE"'", "header_row": '"$HEADER_ROW"',
  "column_mapping": [{"index": 0, "role": "name"}, {"index": 1, "role": "characteristics"},
                     {"index": 2, "role": "quantity"}, {"index": 3, "role": "unit"}]
}' | tee job.json | jq '{job_id, status}'
JOB=$(jq -r .job_id job.json)
```

**3. Прогресс** — опрашивать раз в 1–2 секунды, пока статус `queued`, `parsing` или `processing`.

```bash
curl -s $API/jobs/$JOB | jq '{status, total_items, processed_items, failed_items, progress, eta_seconds, summary}'
```

**4. Позиции** — доступны и во время обработки. Фильтры: `band` (green, yellow, red), `status`, `q`, `flag`,
`group_id`, `missing_required=true`; `sort=confidence_asc` поднимает наверх спорные и упавшие позиции.
`missing_required_count` в позиции и `summary.missing_required` в задаче отражают пропуски обязательных
характеристик итогового кода. `missing_required=false` оставляет список без этого ограничения. Сводка и фильтр
считают только факт пропуска, без сборки JSONB характеристик, и отключают JIT на свою транзакцию: на 1000 позиций
это десятки миллисекунд вместо секунд на полной выборке.

```bash
curl -s "$API/jobs/$JOB/items?sort=confidence_asc&page_size=10" \
  | jq '.total, (.items[] | {row_index, raw_text, status, band, final: .final.ktru_code, error})'
ITEM=$(curl -s "$API/jobs/$JOB/items?page_size=1" | jq -r '.items[0].item_id')
curl -s $API/items/$ITEM | jq '{raw_text, extracted, predictions: [.predictions[] | {rank, ktru_code, name, confidence}]}'
curl -s "$API/jobs/$JOB/items?missing_required=true" | jq '.total, .items[].missing_required_count'
```

**Характеристики итогового кода.** GET возвращает обязательные, найденные в тексте необязательные и необязательные
с ручным значением. PUT принимает имена из каталога выбранного кода; пробелы по краям обрезаются, `null`, пустая
строка и пробелы снимают ручное значение. Значения сохраняются отдельно для каждого кода позиции.

```bash
curl -s "$API/items/$ITEM/characteristics" | jq '{ktru_code, missing_required_count, characteristics}'
CODE=$(curl -s "$API/items/$ITEM/characteristics" | jq -r .ktru_code)
curl -s -X PUT "$API/items/$ITEM/characteristics" -H 'Content-Type: application/json' \
  -d '{"ktru_code":"'"$CODE"'","values":{"ОЗУ":"16 ГБ"}}' | jq .missing_required_count
```

**5. Решения.** Подтвердить предложенный код, указать свой (код ищется в каталоге задачи), подтвердить всё не ниже
порога. Позиции с предупреждениями массово не подтверждаются, пока не передать `"skip_flagged": false`.

```bash
curl -s -X POST $API/items/$ITEM/confirm | jq '{status, final}'
VERSION=$(jq -r .catalog_version_id job.json)
# Кириллицу в адресе curl сам не кодирует, а uvicorn такой запрос отвергает — отсюда --data-urlencode.
curl -s --get --data-urlencode "q=бумага" --data-urlencode "version_id=$VERSION" $API/catalog/search \
  | jq '.items[] | {ktru_code, name}'
curl -s -X PUT $API/items/$ITEM/decision -H 'Content-Type: application/json' \
  -d '{"ktru_code": "99.99.04.000-00000001", "reason": "так в ТЗ"}' | jq '{status, final}'
curl -s -X POST $API/jobs/$JOB/confirm-bulk -H 'Content-Type: application/json' -d '{"min_confidence": 0.85}'
```

**6. Отмена и удаление.**

```bash
curl -s -X POST $API/jobs/$JOB/cancel | jq '{status}'   # 409 invalid_state, если задача уже завершена
curl -s -o /dev/null -w '%{http_code}\n' -X DELETE $API/jobs/$JOB   # 204
```

**7. Выгрузка** — когда задача завершена (`done`, `done_with_errors`) или отменена. `format`: `source` (как загружен;
для pdf — xlsx), `xlsx`, `csv`, `json`, `application` (xlsx для заявки). Число непроверенных позиций — в заголовке
`X-Unreviewed-Count`, число позиций с незаполненными обязательными характеристиками — в
`X-Missing-Required-Count`. Пропуски не блокируют выгрузку.

```bash
curl -s -D headers.txt -o result.xlsx "$API/jobs/$JOB/export"
grep -i -E 'x-unreviewed-count|x-missing-required-count|content-disposition' headers.txt
curl -s "$API/jobs/$JOB/export?format=json" | jq '.[0]'
curl -s -o application.xlsx "$API/jobs/$JOB/export?format=application"
curl -s "$API/jobs/$JOB/export?allow_unreviewed=false" | jq .error   # 409 unreviewed_items, если такие остались
```

Формат `application` собирает `backend/files` через `export_job_results`.

**Комплект файлов.** Загрузите файлы через `POST /files` и сохраните их `file_id` в `FILE1` и `FILE2`, затем создайте
комплект из обычных задач. Максимум файлов
показывает `/config` в `limits.max_files_per_batch` (по умолчанию 10). В ответе `jobs` идут в порядке создания;
каждую задачу можно открыть, отменить или удалить обычными маршрутами. `GET /batches/{id}` возвращает общие
статус, прогресс и сводку. Экспорт доступен после завершения или отмены всех задач комплекта.

```bash
curl -s "$API/config" | jq .limits.max_files_per_batch
curl -s -X POST "$API/batches" -H 'Content-Type: application/json' -d '{
  "name": "Закупка оргтехники",
  "jobs": [
    {"file_id": "'"$FILE1"'", "column_mapping": [{"index": 0, "role": "name"}]},
    {"file_id": "'"$FILE2"'", "column_mapping": [{"index": 0, "role": "name"}]}
  ]
}' | tee batch.json | jq '{batch_id, status, jobs: [.jobs[].job_id]}'
BATCH=$(jq -r .batch_id batch.json)
curl -s "$API/batches/$BATCH" | jq '{status, progress, summary}'
curl -s "$API/jobs?batch_id=$BATCH&page_size=100" | jq '.items[].job_id'
curl -s -D batch-headers.txt -o batch.xlsx "$API/batches/$BATCH/export?format=application"
```

**8. Быстрая проверка** — до 20 строк сразу, без задачи и без сохранения (экран загрузки).

```bash
curl -s -X POST $API/classify -H 'Content-Type: application/json' \
  -d '{"items": [{"text": "Бумага офисная А4, 80 г/м2, 500 листов"}, {"text": "Картридж HP 85A"}]}' \
  | jq '.results[] | {text, code: .predictions[0].ktru_code, confidence: .predictions[0].confidence}'
```

**9. Качество и статистика.** Оценку на наборе `dev` или `test` считает worker-ml; разметка — в
`contracts/fixtures/eval/*.jsonl` (её загружает migrate).

```bash
curl -s -X POST $API/quality/run -H 'Content-Type: application/json' -d '{"split": "dev"}' | jq '{run_id, status}'
curl -s $API/quality/latest | jq '.dev | {status, metrics}'   # status: running → done
curl -s $API/stats | jq
```

**10. Новая версия каталога** без перезапуска: JSONL, одна позиция `CatalogRow` на строку. worker-io загружает строки,
worker-ml строит индекс; версия становится `ready`, и новые задачи работают с ней. Большой файл — напрямую в API
(через nginx на порту интерфейса проходит не больше 60 МБ).

```bash
curl -s -X POST $API/catalog/import -F file=@catalog.jsonl -F 'note=КТРУ на 29.10' | jq '{version_id, status}'
curl -s $API/catalog/versions | jq '.items[] | {version_id, status, items_count, note}'
```

Ошибки всегда в одном формате `{"error": {"code", "message", "details"}}`, `message` — по-русски для человека.
