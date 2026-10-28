# backend/ — API, очереди, миграции

Владелец — Данил; `backend/files/` (разбор файлов и экспорт) — Донир. Общие правила — в корневом `CLAUDE.md`, контракты — в `contracts/` (они важнее этого текста).

## Структура

| Путь | Что там |
| --- | --- |
| `app/main.py` | `create_app()`: роутеры под `/api/v1`, обработчики ошибок, middleware, lifespan (движок БД, Redis, очередь Celery) |
| `app/errors.py` | `ApiError` и единый формат `{"error": {"code", "message", "details"}}`; валидация → 400 `invalid_request` с текстом по-русски |
| `app/middleware.py` | request_id и строка лога на запрос, заголовки безопасности, лимит тела (413 `file_too_large`) |
| `app/routes/` | маршруты по тегам `openapi.yaml`: `files.py` (`POST /files`), `jobs.py` (`/jobs`, отмена, удаление), `items.py` (позиции и решения: confirm, decision, confirm-bulk), `catalog.py` (поиск, позиция КТРУ, версии), `export.py` (выгрузка через `backend/files`), `system.py` (`/health`, `/config`) |
| `app/views.py` | сборка ответов `Job` и `Item` из таблиц: summary, ETA, цвет, предсказания с наименованиями из каталога задачи, `final` из `v_item_final` |
| `app/health.py` | проверки для `/health`: база, Redis, готовность worker-ml, каталог |
| `core/settings.py` | все переменные окружения (`Settings`); список с пояснениями — `.env.example` |
| `core/models.py` | SQLAlchemy-модели таблиц `schema.sql` (схему создают миграции, не модели) |
| `core/catalog.py` | импорт каталога из JSONL (`CatalogRow`) — для migrate и будущего `POST /catalog/import`; `download_catalog` — полный КТРУ через `ml.fetch_catalog` |
| `core/ml.py` | `load_pipeline()` (stub/real), `catalog_fallback()` — откат на заглушку без каталога, `pipeline_mode()` / `describe_mode()` — режим для `/config` (у конвейера, иначе по переменным), `check_results()` — проверка ответа ML по контракту |
| `core/readiness.py` | готовность worker-ml в Redis (heartbeat с TTL) |
| `core/uploads.py` | хранилище файлов на томе uploads (`<file_id>.<расширение>`), очистка имени, ошибки `backend/files` → код и текст для человека |
| `core/queue.py` | постановка задач по имени (`io.parse_file`, `ml.process_chunk`); в тестах — `FakeQueue` из `tests/conftest.py` |
| `core/processing.py` | работа воркеров: разбор → items, пачки по 50 через ML → predictions, итоговый статус, watchdog; цвет и статус по порогам; `ExampleFeed` — досылка подтверждённых примеров в конвейер перед пачкой (FR-4.7) |
| `tasks/celery_app.py` | Celery: `create_celery(settings)`, очереди `io`/`ml`, JSON, `acks_late`, лимиты, beat |
| `tasks/jobs.py`, `tasks/watchdog.py` | задачи io: `io.parse_file`, `io.watchdog`; логика — в `core/processing.py` |
| `tasks/ml_worker.py` | вход worker-ml: загрузка конвейера, готовность, `ml.process_chunk` с ретраями 5/20/60 с |
| `migrations/` | Alembic; `0001` — замороженная копия `contracts/schema.sql` (`sql/0001_initial.sql`) |
| `migrate.py` | контейнер migrate: миграции → роль приложения → каталог (в режиме real скачивает его на том `catalog`, не вышло — заглушка) |
| `Dockerfile` | два образа: по умолчанию `ktru-backend` (api, worker-io, beat, migrate), `--target ml` — `ktru-backend-ml` для worker-ml (torch CPU, `requirements-ml.txt`, модель e5-small из `fetch_embedder.py`) |

## Команды (из корня репозитория)

```bash
uv venv --python 3.12 .venv && VIRTUAL_ENV=.venv uv pip install --require-hashes -r backend/requirements-dev.txt
.venv/bin/ruff check backend && .venv/bin/ruff format backend
.venv/bin/pytest backend/tests                      # без TEST_DATABASE_URL тесты с базой пропускаются
docker run -d --rm --name ktru-test-pg -e POSTGRES_USER=ktru_test -e POSTGRES_PASSWORD=ktru_test \
  -e POSTGRES_DB=ktru_test -p 127.0.0.1:55432:5432 pgvector/pgvector:0.8.2-pg16-trixie
TEST_DATABASE_URL=postgresql+psycopg://ktru_test:ktru_test@127.0.0.1:55432/ktru_test .venv/bin/pytest backend/tests
docker compose up --build                           # всё на заглушке; http://127.0.0.1:8000/api/v1/health
```

Зависимости меняются только в `requirements*.in`, затем lock пересобирается (с хешами для всех платформ; torch — CPU-сборка из индекса PyTorch):

```bash
cd backend && uv pip compile requirements.in --universal --python-version 3.12 --generate-hashes --no-header -o requirements.txt \
  && uv pip compile requirements-dev.in --universal --python-version 3.12 --generate-hashes --no-header -o requirements-dev.txt \
  && uv pip compile requirements-ml.in --universal --python-version 3.12 --generate-hashes --no-header --torch-backend cpu -o requirements-ml.txt
```

`PIPELINE=real` — режим по умолчанию в compose: migrate при первом запуске скачивает полный КТРУ на том `catalog` (`/data/catalog/catalog.jsonl`), worker-ml читает его оттуда. Не скачался — оба работают на заглушке, причина — в `/health` (`components.models.detail`). Чтобы скачать заново, удалите том: `docker volume rm ktru_catalog`. `PIPELINE=stub` — заглушка без скачивания. Свой файл каталога можно смонтировать в migrate и worker-ml через `docker-compose.override.yml` (он в `.gitignore`):

```yaml
services:
  migrate:
    volumes: ["./data/ktru/ktru_data:/srv/data/ktru:ro"]
  worker-ml:
    volumes: ["./data/ktru/ktru_data:/srv/data/ktru:ro"]
```

и в `.env`: `PIPELINE=real`, `KTRU_CATALOG_PATH=/srv/data/ktru/catalog.jsonl`.

## Правила

- Эндпоинты строго по `contracts/openapi.yaml`; перед PR сверить ответы со схемами (`tests/openapi.py: assert_matches_schema`).
- Ошибки — только `raise ApiError(...)` (или `not_found`, `invalid_state`, `not_ready`); `HTTPException` не бросаем. `message` — по-русски, для человека, без внутренних подробностей.
- Эндпоинты синхронные (`def`), один драйвер psycopg 3 — и в API, и в воркерах.
- Схема меняется только новой ревизией Alembic вместе с PR в `contracts/schema.sql`; тест `test_migrations_match_contract` сверяет их.
- Из `ml/` импортируется только `ml.pipeline.get_pipeline()` (через `core/ml.py`); каталог migrate скачивает отдельным процессом `python -m ml.fetch_catalog`. Каждый ответ ML проходит `check_results()`. Методы конвейера сверх контракта (`add_examples`, `describe_mode`) вызываются только через `getattr`: у заглушки их нет.
- Аргументы задач Celery — только JSON-типы (id, строки); воркер меняет позиции только `WHERE status = 'pending'`.
- Переход статуса задачи — один `UPDATE ... WHERE status IN (...)`: повторная доставка, гонка двух воркеров, отмена и удаление посреди работы не должны ломать состояние (тесты — `tests/test_processing.py`).
- Решения только добавляются в `decisions`; итоговый код — `v_item_final`. Ручное решение (`via = manual`) пишет пример в `confirmed_examples` (upsert по `source_item_id`, `norm_text` — только `normalize_text`), массовое (`via = bulk`) — нет. Повтор того же ручного решения не создаёт новой строки.
- Порог green сравнивается со значением колонки `real` как `real` (`processing.as_real`, в SQL — `CAST(... AS real)`): иначе статус от воркера и цвет в API разойдутся ровно на пороге.
- Секреты — только из переменных окружения; каждому сервису compose передаются только нужные ему (ключ LLM — только worker-ml, пароль владельца БД — только migrate). В логах не бывает паролей и текстов исключений для пользователя.
- Данные КТРУ (`data/`, `context/`) не коммитим и не кладём в тесты: в тестах — `contracts/fixtures/` и искусственные строки.

## Частые ошибки

- Модели грузятся в `worker_process_init`, а Celery по умолчанию ждёт старта дочернего процесса 4 с — поэтому `worker_proc_alive_timeout` задан в `tasks/ml_worker.py`.
- `pg_isready` через сокет проходит ещё во время initdb — healthcheck postgres проверяет по TCP (`-h 127.0.0.1`).
- Движок БД и клиент Redis в воркере создаются после fork (`core/db.worker_engine`), не на уровне модуля.
- `Settings.model_copy(update=...)` не валидирует: секреты в тестах передавайте как `SecretStr`.
- FastAPI превращает любую ошибку чтения тела в 400 — превышение лимита распознаётся по `__cause__` (`BodyTooLarge`) и остаётся 413.
- `nullable: true` вместе с `enum` или `allOf` валидатор OpenAPI 3.0 понимает строго; `tests/openapi.py` разрешает там null, как задумано контрактом.
- `backend/files` определяет формат по расширению пути — файл в хранилище лежит с расширением. Ошибки библиотек он оборачивает через `from e`: их текст пользователю не показываем (`uploads.file_error`).
