# 4. Модель данных

Данные хранятся в одной Postgres с расширениями pgvector и pg\_trgm: одиннадцать таблиц (включая file\_layouts для запомненной раскладки колонок) в четырёх группах. Полная схема — в файле `schema.sql` в репозитории; этот раздел даёт карту.

| Группа | Таблица | Назначение | Ключевые поля |
| --- | --- | --- | --- |
| Каталог | `catalog_versions` | Версия загруженного снимка КТРУ | id, source, status, items\_count, loaded\_at |
| Каталог | `catalog_items` | Позиции КТРУ с векторами | ktru\_code, okpd2\_code, name, characteristics (jsonb), embedding (vector 1024), tsv |
| Обработка | `source_files` | Загруженные файлы | filename, mime\_type, size\_bytes, sha256, storage\_path, parsed\_meta |
| Обработка | `jobs` | Задача обработки одного файла | status, column\_mapping, options, total\_items, processed\_items, failed\_items, catalog\_version\_id, error\_code, updated\_at (триггер) |
| Обработка | `items` | Одна позиция спецификации | job\_id, row\_index, raw\_text, extracted (jsonb), status, path (fast / rerank) |
| Обработка | `predictions` | Топ-3 кандидата на позицию | item\_id, rank, ktru\_code, confidence, features (jsonb), characteristic\_matches, explanation |
| Обратная связь | `decisions` | Подтверждения и правки пользователя | item\_id, kind (confirm / correct), ktru\_code, created\_at |
| Обратная связь | `confirmed_examples` | Эталонные пары «текст → код» из правок, с вектором | text, embedding, ktru\_code, source\_item\_id |
| Качество | `eval_samples` | Размеченный набор | split (dev / test), raw\_text, expected\_ktru\_code, expected\_attrs |
| Качество | `eval_runs` | Результаты прогонов оценки | config (jsonb), metrics (jsonb), created\_at |

**Правила, которые важны для всех трёх команд**

- **Итоговый ответ по позиции** = последняя запись в `decisions`, если она есть, иначе `predictions` с rank = 1. Это одно правило, и бэкенд применяет его в единственном месте — представлении `v_item_final`.
- **Версия каталога** фиксируется в задаче при старте. Новая загрузка каталога не ломает уже идущие задачи.
- **Размерность вектора** задаётся моделью эмбеддингов (в схеме пока 1024). Смена модели с другой размерностью требует миграции и полной переиндексации каталога, поэтому это решение ML-команды в первые дни.
- **Все идентификаторы** — UUID, кроме самого кода КТРУ (текст). Время — UTC.
- **Миграции** — Alembic; таблицы из `schema.sql` — это целевое состояние к концу недели 1, после этого меняется только через миграции.
- **Индексы:** HNSW по `embedding`, GIN по `tsv`, trigram-индекс по `name`, уникальный индекс `(version_id, ktru_code)`.
