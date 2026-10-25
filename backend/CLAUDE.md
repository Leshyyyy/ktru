# backend/ — API, очереди, миграции

Владелец — Данил; `backend/files/` (разбор файлов и экспорт) — Донир. Общие правила — в корневом `CLAUDE.md`, контракты — в `contracts/` (они важнее этого текста).

## Структура

| Путь | Что там |
| --- | --- |
| `app/main.py` | `create_app()`: роутеры под `/api/v1`, обработчики ошибок, middleware, lifespan (движок БД, Redis) |
| `app/errors.py` | `ApiError` и единый формат `{"error": {"code", "message", "details"}}`; валидация → 400 `invalid_request` с текстом по-русски |
| `app/middleware.py` | request_id и строка лога на запрос, заголовки безопасности, лимит тела (413 `file_too_large`) |
| `app/routes/` | маршруты по тегам `openapi.yaml`; сейчас `system.py` (`/health`, `/config`) |
| `app/health.py` | проверки для `/health`: база, Redis, готовность worker-ml, каталог |
| `core/settings.py` | все переменные окружения (`Settings`); список с пояснениями — `.env.example` |
| `core/models.py` | SQLAlchemy-модели таблиц `schema.sql` (схему создают миграции, не модели) |
| `core/catalog.py` | импорт каталога из JSONL (`CatalogRow`) — для migrate и будущего `POST /catalog/import` |
| `core/ml.py` | `load_pipeline()` (stub/real), `describe_mode()`, `check_results()` — проверка ответа ML по контракту |
| `core/readiness.py` | готовность worker-ml в Redis (heartbeat с TTL) |
| `tasks/celery_app.py` | Celery: очереди `io`/`ml`, JSON, `acks_late`, лимиты, beat; `ml_worker.py` — вход worker-ml |
| `migrations/` | Alembic; `0001` — замороженная копия `contracts/schema.sql` (`sql/0001_initial.sql`) |
| `migrate.py` | контейнер migrate: миграции → роль приложения → стартовый каталог |

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

Зависимости меняются только в `requirements*.in`, затем lock пересобирается (с хешами для всех платформ):

```bash
cd backend && uv pip compile requirements.in --universal --python-version 3.12 --generate-hashes --no-header -o requirements.txt \
  && uv pip compile requirements-dev.in --universal --python-version 3.12 --generate-hashes --no-header -o requirements-dev.txt
```

Режим `PIPELINE=real` локально: смонтируйте каталог в migrate и worker-ml через `docker-compose.override.yml` (он в `.gitignore`):

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
- Из `ml/` импортируется только `ml.pipeline.get_pipeline()` (через `core/ml.py`); каждый ответ ML проходит `check_results()`.
- Аргументы задач Celery — только JSON-типы (id, строки); воркер меняет позиции только `WHERE status = 'pending'`.
- Секреты — только из переменных окружения; каждому сервису compose передаются только нужные ему (ключ LLM — только worker-ml, пароль владельца БД — только migrate). В логах не бывает паролей и текстов исключений для пользователя.
- Данные КТРУ (`data/`, `context/`) не коммитим и не кладём в тесты: в тестах — `contracts/fixtures/` и искусственные строки.

## Частые ошибки

- Модели грузятся в `worker_process_init`, а Celery по умолчанию ждёт старта дочернего процесса 4 с — поэтому `worker_proc_alive_timeout` задан в `tasks/ml_worker.py`.
- `pg_isready` через сокет проходит ещё во время initdb — healthcheck postgres проверяет по TCP (`-h 127.0.0.1`).
- Движок БД и клиент Redis в воркере создаются после fork (`core/db.worker_engine`), не на уровне модуля.
- `Settings.model_copy(update=...)` не валидирует: секреты в тестах передавайте как `SecretStr`.
- FastAPI превращает любую ошибку чтения тела в 400 — превышение лимита распознаётся по `__cause__` (`BodyTooLarge`) и остаётся 413.
