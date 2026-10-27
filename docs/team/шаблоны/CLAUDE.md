# AI-классификатор КТРУ

Кейс-чемпионат B2B-РТС, задача 5. Пользователь загружает спецификацию (xlsx, csv, pdf), подтверждает роли колонок; система асинхронно извлекает атрибуты и подбирает код КТРУ (топ-3, уверенность 0–1, пояснение); человек подтверждает или правит код и выгружает файл. Авторизации нет.

## Источник правды

Контракты важнее любого текста: `contracts/openapi.yaml` (API, префикс `/api/v1`), `contracts/schema.sql` (БД), `contracts/ml_contracts.py` (интерфейс ML), `contracts/stub_pipeline.py` (заглушка ML). Если код или документ расходится с контрактом — прав контракт.

## Документы (читай нужный раздел, а не всё подряд)

- `docs/analytics/01-goals.md` — границы и допущения
- `docs/analytics/02-scenario.md` — экраны и статусы
- `docs/analytics/05-api.md`, `06-async.md` — API, задачи Celery, надёжность
- `docs/analytics/07-ml.md`, `09-data-quality.md` — конвейер ML, данные, метрики
- `docs/analytics/08-files.md` — разбор входных файлов
- `docs/analytics/10-nfr.md`, `11-acceptance.md` — нефункциональные требования и матрица приёмки
- `docs/analytics/14-extras.md` — принятые доработки и правки контрактов
- `docs/team/` — распределение задач, макеты экранов

## Стек

Python, FastAPI, Pydantic, Alembic, Celery + Redis, PostgreSQL 16 + pgvector ≥ 0.8 + pg_trgm; React 19 + TypeScript + Vite + Mantine + TanStack Query; docker-compose.

## Команды

- `docker-compose up --build` — всё (CPU, без ключей LLM → режим degraded)
- `cd frontend && npm run dev` — фронт на встроенном моке; `npm run dev:backend` — против API на :8000
- `python -m ml.evaluate --split dev` — метрики ML
- `pytest` — тесты бэкенда, ML и разбора файлов
- `sphinx-build -W -b html docs docs/_build/html` — документация

## Правила

- Работай только в папке своей задачи (`backend/`, `backend/files/`, `ml/`, `frontend/`, `tests/`); в соседние — только читать.
- `contracts/` не меняй. Нужна правка — скажи, я согласую её отдельным PR.
- Ошибки API — `{"error": {"code", "message", "details"}}`, `message` по-русски для человека. Валидация запроса — 400 `invalid_request`, 422 — только бизнес-ошибки.
- Уверенность — число 0–1; зону (green / yellow / red) считает бэкенд по порогам из `/config`.
- Итоговый код позиции — только через представление `v_item_final`.
- Celery-задачи идемпотентны; `predictions` пишутся по ключу `(item_id, rank)`; воркер меняет только позиции в `pending`.
- ML-код в `ml/` не импортирует FastAPI и Celery.
- Новая логика — с тестом; перед словом «готово» запусти тесты своей папки.
- Не читай файлы из `data/` целиком; для примера бери первые строки.
- Секреты только из переменных окружения.
- Большое изменение — сначала план.
- Git: не делай push, force-push, rebase и удаление веток без явной просьбы.

## Владельцы

ML — Jove; бэкенд, БД, compose — Данил; разбор файлов, экспорт, e2e — Донир; фронтенд — Таир; данные, приёмка, README, видео — Егор.
