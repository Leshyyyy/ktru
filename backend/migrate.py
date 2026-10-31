"""Разовый контейнер migrate: миграции, роль приложения в БД, стартовый каталог и разметка для оценки качества.
`python -m backend.migrate`.

Остальные сервисы compose ждут его успешного завершения. Повторный запуск ничего не ломает и не дублирует.
"""

from __future__ import annotations

import logging
import sys
import time
from pathlib import Path

from alembic import command
from alembic.config import Config
from psycopg import sql
from sqlalchemy import Engine, select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from backend.core.catalog import (
    MIGRATE_SOURCE,
    CatalogImportError,
    download_catalog,
    file_sha256,
    import_catalog_file,
)
from backend.core.db import make_engine
from backend.core.logs import setup_logging
from backend.core.ml import catalog_fallback
from backend.core.models import CatalogVersion
from backend.core.quality import seed_eval_samples
from backend.core.settings import ConfigError, Settings, get_settings

log = logging.getLogger("backend.migrate")

ALEMBIC_INI = Path(__file__).resolve().parent / "alembic.ini"
# Ключ pg_advisory_lock: два migrate одновременно (например, при --scale) не столкнутся.
_LOCK_KEY = 5_250_026_001


def main(settings: Settings | None = None) -> int:
    settings = settings or get_settings()
    setup_logging(settings.log_level)
    try:
        engine = make_engine(settings.owner_database_url(), application_name="ktru-migrate", null_pool=True)
        _wait_for_database(engine)
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as lock:
            lock.execute(text("SELECT pg_advisory_lock(:key)"), {"key": _LOCK_KEY})
            try:
                upgrade(engine)
                ensure_app_role(engine, settings)
                seed_catalog(engine, prepare_catalog(settings))
                seed_eval_samples(engine, settings.eval_samples_dir)
            finally:
                lock.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": _LOCK_KEY})
    except (ConfigError, CatalogImportError) as e:
        log.error("migrate не выполнен: %s", e)
        return 1
    except Exception:
        log.exception("migrate не выполнен")
        return 1
    log.info("migrate завершён")
    return 0


def _wait_for_database(engine: Engine, timeout_s: float = 60) -> None:
    deadline = time.monotonic() + timeout_s
    while True:
        try:
            with engine.connect() as conn:
                conn.exec_driver_sql("SELECT 1")
            return
        except OperationalError:
            if time.monotonic() > deadline:
                raise
            log.info("База ещё не принимает подключения, жду")
            time.sleep(1)


def upgrade(engine: Engine) -> None:
    config = Config(str(ALEMBIC_INI))
    with engine.begin() as conn:
        config.attributes["connection"] = conn
        command.upgrade(config, "head")
    log.info("Схема БД в актуальном состоянии")


def ensure_app_role(engine: Engine, settings: Settings) -> None:
    """Роль, которой работают api и воркеры: читать и писать данные можно, менять схему — нет."""
    if settings.app_db_password is None:
        raise ConfigError("Не задан APP_DB_PASSWORD — пароль роли приложения в PostgreSQL")
    owner, role = settings.postgres_user, settings.app_db_user
    if role == owner:
        raise ConfigError("APP_DB_USER должен отличаться от POSTGRES_USER: приложению не нужны права владельца схемы")

    ident, owner_ident = sql.Identifier(role), sql.Identifier(owner)
    with engine.begin() as conn, conn.connection.driver_connection.cursor() as cursor:
        # На сервер уходит SCRAM-хеш, посчитанный libpq на клиенте (как \password в psql), а не сам пароль:
        # упавшая команда попадает в лог PostgreSQL целиком, и пароль в открытом виде оказался бы там.
        verifier = cursor.connection.pgconn.encrypt_password(
            settings.app_db_password.get_secret_value().encode(), role.encode(), b"scram-sha-256"
        )
        attributes = sql.SQL("LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD {}").format(
            sql.Literal(verifier.decode())
        )
        cursor.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,))
        verb = "ALTER" if cursor.fetchone() else "CREATE"
        # Пароль пересылается каждый раз: сменили APP_DB_PASSWORD — роль получила новый.
        cursor.execute(sql.SQL("{} ROLE {} WITH {}").format(sql.SQL(verb), ident, attributes))
        for statement in (
            "REVOKE CREATE ON SCHEMA public FROM PUBLIC",
            "GRANT CONNECT ON DATABASE {db} TO {role}",
            "GRANT USAGE ON SCHEMA public TO {role}",
            "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {role}",
            "REVOKE ALL ON TABLE alembic_version FROM {role}",
            # Таблицы будущих ревизий получат те же права автоматически.
            "ALTER DEFAULT PRIVILEGES FOR ROLE {owner} IN SCHEMA public "
            "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {role}",
        ):
            cursor.execute(
                sql.SQL(statement).format(db=sql.Identifier(settings.postgres_db), role=ident, owner=owner_ident)
            )
    log.info("Роль приложения в БД настроена", extra={"role": role, "action": verb.lower()})


def prepare_catalog(settings: Settings) -> Settings:
    """PIPELINE=real: полного каталога ещё нет на томе — скачать его. Не вышло (нет сети, Hugging Face недоступен,
    не совпал sha256) — откат на заглушку: система запускается, а /health объясняет, почему коды 99.99.*.
    """
    if settings.pipeline != "real":
        return settings
    if settings.ktru_catalog_path is None:
        raise ConfigError("PIPELINE=real: задайте KTRU_CATALOG_PATH — тот же файл читает worker-ml")
    if not settings.ktru_catalog_path.is_file():
        log.info("Скачиваю каталог КТРУ", extra={"path": str(settings.ktru_catalog_path)})
        download_catalog(settings.ktru_catalog_path)
    if reason := catalog_fallback(settings):
        log.error(reason)
        return settings.model_copy(update={"pipeline": "stub"})
    return settings


def seed_catalog(engine: Engine, settings: Settings) -> None:
    """Стартовый каталог: фикстура для PIPELINE=stub, иначе файл KTRU_CATALOG_PATH (его же читает worker-ml).

    Версия с тем же sha256 файла уже загружена — ничего не делаем; поменялся файл — появится новая версия.
    """
    if settings.pipeline == "stub" and settings.ktru_catalog_path:
        log.warning("PIPELINE=stub: KTRU_CATALOG_PATH не используется, загружаю каталог заглушки")
    path = settings.catalog_seed_path()
    if not path.is_file():
        raise ConfigError(f"Файл каталога не найден: {path}")
    note = f"sha256:{file_sha256(path)}"
    with Session(engine) as session, session.begin():
        existing = session.scalar(
            select(CatalogVersion.id).where(CatalogVersion.status == "ready", CatalogVersion.note == note).limit(1)
        )
        if existing is not None:
            log.info("Каталог уже загружен", extra={"version_id": str(existing), "file": path.name})
            return
        version = import_catalog_file(session, path, source=f"{MIGRATE_SOURCE}{path.name}", note=note)
        log.info(
            "Каталог загружен",
            extra={"version_id": str(version.id), "items": version.items_count, "file": path.name},
        )


if __name__ == "__main__":
    sys.exit(main())
