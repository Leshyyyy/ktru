-- ============================================================================
-- AI-классификатор КТРУ · схема БД (PostgreSQL 15+ с расширениями pgvector, pg_trgm)
--
-- Это целевое состояние к концу недели 1. Дальше меняется только через миграции Alembic.
-- Владельцы: бэкенд — всё, кроме блоков «Каталог», «Обратная связь» и «Качество»;
--            ML — catalog_items, confirmed_examples, eval_*.
-- Размерность вектора (1024) должна совпадать с EMBEDDING_DIM. Смена модели с другой
-- размерностью = миграция + полная переиндексация каталога и confirmed_examples.
-- ============================================================================

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- ---------------------------------------------------------------------------
-- Каталог
-- ---------------------------------------------------------------------------

CREATE TABLE catalog_versions (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    source        text        NOT NULL,                       -- откуда загружено (имя файла, ссылка)
    status        text        NOT NULL DEFAULT 'importing'
                  CHECK (status IN ('importing', 'ready', 'failed')),
    items_count   integer     NOT NULL DEFAULT 0,
    note          text,
    loaded_at     timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE catalog_items (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    version_id      uuid         NOT NULL REFERENCES catalog_versions(id) ON DELETE CASCADE,
    ktru_code       text         NOT NULL,                    -- 26.20.15.000-00000001
    okpd2_code      text         NOT NULL,                    -- 26.20.15.000
    name            text         NOT NULL,
    description     text,
    unit            text,
    -- Если выгрузка содержит статус кода: archived → предупреждение archived_code у позиции.
    status          text         NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'archived')),
    replaced_by     text,                                     -- код-преемник, если известен
    -- [{"name": "...", "values": ["..."], "required": true}]
    characteristics jsonb        NOT NULL DEFAULT '[]'::jsonb,
    search_text     text         NOT NULL,                    -- то, что превращается в эмбеддинг и индексируется
    embedding       vector(1024),
    tsv             tsvector GENERATED ALWAYS AS (to_tsvector('russian', search_text)) STORED,
    UNIQUE (version_id, ktru_code)
);

-- ВНИМАНИЕ: поиск всегда фильтруется по version_id. HNSW сначала берёт ближайших соседей
-- (hnsw.ef_search, по умолчанию 40), а фильтр применяется потом — при нескольких версиях
-- в таблице запрос может вернуть меньше top_k кандидатов. Решения:
--   1) pgvector >= 0.8 и в сессии поиска: SET hnsw.iterative_scan = relaxed_order;
--   2) после перехода на новую версию обнулять embedding у старых версий (когда их задачи завершены).
CREATE INDEX catalog_items_embedding_hnsw ON catalog_items USING hnsw (embedding vector_cosine_ops);
CREATE INDEX catalog_items_tsv_gin        ON catalog_items USING gin (tsv);
CREATE INDEX catalog_items_name_trgm      ON catalog_items USING gin (name gin_trgm_ops);
CREATE INDEX catalog_items_okpd2          ON catalog_items (version_id, okpd2_code);

-- ---------------------------------------------------------------------------
-- Обработка
-- ---------------------------------------------------------------------------

CREATE TABLE source_files (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    filename      text        NOT NULL,
    mime_type     text        NOT NULL,
    size_bytes    bigint      NOT NULL,
    sha256        text        NOT NULL,                       -- повторная загрузка того же файла → duplicate_of_job_id
    storage_path  text        NOT NULL,
    -- результат предразбора: листы, колонки, предложенные роли (то, что вернул POST /files)
    parsed_meta   jsonb       NOT NULL DEFAULT '{}'::jsonb,
    created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX source_files_sha256 ON source_files (sha256);

-- Запомненная раскладка колонок: ключ — хеш нормализованных заголовков. Сохраняется при создании задачи.
CREATE TABLE file_layouts (
    header_hash     text PRIMARY KEY,
    header_row      integer     NOT NULL DEFAULT 0,
    column_mapping  jsonb       NOT NULL,
    updated_at      timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE jobs (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    file_id             uuid        NOT NULL REFERENCES source_files(id),
    catalog_version_id  uuid        NOT NULL REFERENCES catalog_versions(id),
    status              text        NOT NULL DEFAULT 'queued'
                        CHECK (status IN ('queued', 'parsing', 'processing', 'done',
                                          'done_with_errors', 'failed', 'cancelled')),
    -- {"sheet": "...", "header_row": 0, "columns": [{"index": 0, "role": "name"}, ...]}
    column_mapping      jsonb       NOT NULL,
    options             jsonb       NOT NULL DEFAULT '{}'::jsonb,
    effective_mode      text        NOT NULL DEFAULT 'normal'
                        CHECK (effective_mode IN ('normal', 'degraded')),
    total_items         integer     NOT NULL DEFAULT 0,
    processed_items     integer     NOT NULL DEFAULT 0,
    failed_items        integer     NOT NULL DEFAULT 0,
    cancel_requested    boolean     NOT NULL DEFAULT false,
    error               text,                                 -- текст для пользователя
    error_code          text        CHECK (error_code IN ('file_unreadable', 'mapping_invalid',
                                                          'limit_exceeded', 'internal_error')),
    created_at          timestamptz NOT NULL DEFAULT now(),
    started_at          timestamptz,
    finished_at         timestamptz,
    -- Обновляется триггером при любом изменении строки (каждая пачка меняет счётчики).
    -- watchdog: задача в queued, parsing или processing с updated_at старше 5 минут → её pending-позиции
    -- (или сам разбор) заново ставятся в очередь. Redis запускается с AOF, чтобы очередь переживала рестарт.
    -- Воркер обновляет позиции только условием WHERE status = 'pending' и завершается тихо, если задачи уже нет.
    updated_at          timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX jobs_created_at ON jobs (created_at DESC);
CREATE INDEX jobs_status     ON jobs (status, updated_at);

CREATE FUNCTION set_updated_at() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at := now();
    RETURN NEW;
END;
$$;

CREATE TRIGGER jobs_set_updated_at BEFORE UPDATE ON jobs
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TABLE items (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id      uuid        NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    row_index   integer     NOT NULL,                         -- номер строки в исходном файле (с 0, после заголовка)
    raw_text    text        NOT NULL,                         -- склейка name + description + characteristics
    raw_row     jsonb       NOT NULL,                         -- исходные значения по колонкам, для экспорта
    extracted   jsonb,                                        -- ExtractedAttributes из ml_contracts.py
    status      text        NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending', 'processed', 'needs_review',
                                  'confirmed', 'corrected', 'failed')),
    path        text        CHECK (path IN ('fast', 'rerank')),
    -- Ключ повторов: lower(name)|lower(brand)|lower(model) из извлечённых атрибутов, NULL если нет бренда или модели.
    -- Наименование входит в ключ, иначе принтер и картридж к нему слились бы в одну группу.
    -- Считает ML (ClassificationResult.norm_key). group_id бэкенд ставит всегда, когда есть norm_key:
    -- uuid5(job_id, norm_key). Одиночная ли позиция, известно только в конце задачи, поэтому group_size
    -- считается при выдаче оконной функцией, а API отдаёт group_id = null при group_size = 1.
    norm_key    text,
    group_id    uuid,
    -- required_missing | archived_code | low_extraction_confidence
    flags       text[]      NOT NULL DEFAULT '{}',
    error       text,
    timings_ms  jsonb,
    created_at  timestamptz NOT NULL DEFAULT now(),
    processed_at timestamptz,                                 -- когда воркер записал результат (ETA, скорость, время ошибок)
    UNIQUE (job_id, row_index)
);

CREATE INDEX items_job_status ON items (job_id, status);
CREATE INDEX items_job_group  ON items (job_id, group_id) WHERE group_id IS NOT NULL;
CREATE INDEX items_flags      ON items USING gin (flags);

CREATE TABLE predictions (
    id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    item_id               uuid        NOT NULL REFERENCES items(id) ON DELETE CASCADE,
    rank                  smallint    NOT NULL CHECK (rank BETWEEN 1 AND 3),
    ktru_code             text        NOT NULL,
    confidence            real        NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    features              jsonb       NOT NULL DEFAULT '{}'::jsonb,   -- признаки калибровки
    characteristic_matches jsonb      NOT NULL DEFAULT '[]'::jsonb,
    explanation           text,
    why_not_chosen        text,                                       -- для rank 2 и 3
    UNIQUE (item_id, rank)                                            -- идемпотентная запись при ретраях
);

-- ---------------------------------------------------------------------------
-- Обратная связь
-- ---------------------------------------------------------------------------

CREATE TABLE decisions (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    item_id     uuid        NOT NULL REFERENCES items(id) ON DELETE CASCADE,
    kind        text        NOT NULL CHECK (kind IN ('confirm', 'correct')),
    -- manual — решение человека по одной позиции; bulk — массовое подтверждение. bulk не пишет
    -- confirmed_examples и не входит в green_correction_rate: иначе модель учится на собственных ответах.
    via         text        NOT NULL DEFAULT 'manual' CHECK (via IN ('manual', 'bulk')),
    ktru_code   text        NOT NULL,
    old_code    text,                                         -- итоговый код до решения (для журнала)
    reason      text,                                         -- необязательный комментарий пользователя
    -- clock_timestamp(), а не now(): now() одинаково для всей транзакции,
    -- и порядок двух решений по одной позиции стал бы неопределённым.
    created_at  timestamptz NOT NULL DEFAULT clock_timestamp()
);

CREATE INDEX decisions_item ON decisions (item_id, created_at DESC);

-- Эталонные пары «текст → код» из подтверждений и правок. Их ищет Retriever как дополнительный источник.
CREATE TABLE confirmed_examples (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    text            text        NOT NULL,
    -- Нормализованный текст (нижний регистр, без лишних пробелов и знаков) — ключ кэша точных совпадений.
    -- Уровень 1 петли обратной связи: совпал ключ → код берётся из примера без поиска.
    -- При оценке качества (eval_runs) кэш и примеры отключены, иначе метрики завышаются.
    norm_text       text        NOT NULL,
    embedding       vector(1024),
    ktru_code       text        NOT NULL,
    source_item_id  uuid        REFERENCES items(id) ON DELETE SET NULL,
    created_at      timestamptz NOT NULL DEFAULT now(),
    -- Одна позиция — один пример: новое решение (подтвердил, затем исправил) делает upsert по source_item_id.
    UNIQUE (source_item_id)
);
-- norm_text считает ТОЛЬКО ml_contracts.normalize_text() (и при записи, и при поиске), иначе кэш не сработает.
-- embedding пишет не бэкенд: задача в воркере ML дозаполняет строки с embedding IS NULL.

CREATE INDEX confirmed_examples_norm_text ON confirmed_examples (norm_text);
CREATE INDEX confirmed_examples_embedding_hnsw ON confirmed_examples USING hnsw (embedding vector_cosine_ops);

-- Итоговый ответ по позиции — единственное место, где применяется правило
-- «последнее решение пользователя, иначе предсказание с rank = 1».
-- Наименование берётся из версии каталога, с которой работает задача.
-- ktru_code = NULL — у позиции нет ни предсказания, ни решения (pending или failed).
CREATE VIEW v_item_final AS
SELECT
    i.id                                                         AS item_id,
    i.job_id,
    COALESCE(d.ktru_code, p.ktru_code)                           AS ktru_code,
    ci.name                                                      AS name,
    CASE WHEN d.id IS NOT NULL THEN 'user' ELSE 'prediction' END AS source,
    CASE WHEN d.id IS NULL THEN p.confidence END                 AS confidence
FROM items i
JOIN jobs j ON j.id = i.job_id
LEFT JOIN LATERAL (
    SELECT id, ktru_code FROM decisions
    WHERE item_id = i.id
    ORDER BY created_at DESC
    LIMIT 1
) d ON true
LEFT JOIN predictions p ON p.item_id = i.id AND p.rank = 1
LEFT JOIN catalog_items ci
       ON ci.version_id = j.catalog_version_id
      AND ci.ktru_code = COALESCE(d.ktru_code, p.ktru_code);

-- ---------------------------------------------------------------------------
-- Качество
-- ---------------------------------------------------------------------------

CREATE TABLE eval_samples (
    id                  text PRIMARY KEY,                     -- s-0042
    split               text        NOT NULL CHECK (split IN ('dev', 'test')),
    raw_text            text        NOT NULL,
    expected_ktru_code  text        NOT NULL,
    -- Заполнено у 50 позиций из 300 — это набор attrs для метрики извлечения атрибутов.
    expected_attrs      jsonb,                                -- {"name": "...", "brand": "...", "model": "..."}
    difficulty          text        CHECK (difficulty IN ('easy', 'medium', 'hard')),
    source              text
);

CREATE TABLE eval_runs (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    split       text        NOT NULL CHECK (split IN ('dev', 'test')),
    status      text        NOT NULL DEFAULT 'running'
                CHECK (status IN ('running', 'done', 'failed')),
    config      jsonb       NOT NULL,                         -- EXTRACTOR, RERANKER, DEVICE, модель, пороги; use_feedback всегда false
    metrics     jsonb,
    created_at  timestamptz NOT NULL DEFAULT now()
);
