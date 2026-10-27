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
`group_id`; `sort=confidence_asc` поднимает наверх спорные и упавшие позиции.

```bash
curl -s "$API/jobs/$JOB/items?sort=confidence_asc&page_size=10" \
  | jq '.total, (.items[] | {row_index, raw_text, status, band, final: .final.ktru_code, error})'
ITEM=$(curl -s "$API/jobs/$JOB/items?page_size=1" | jq -r '.items[0].item_id')
curl -s $API/items/$ITEM | jq '{raw_text, extracted, predictions: [.predictions[] | {rank, ktru_code, name, confidence}]}'
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

Ошибки всегда в одном формате `{"error": {"code", "message", "details"}}`, `message` — по-русски для человека.

Выгрузка результата (`GET /jobs/{id}/export`) появится следующей; до тех пор её можно посмотреть на моке фронтенда
(`cd frontend && npm run dev`).
