"""Миграции, роль приложения и стартовый каталог на настоящем PostgreSQL (маркер db, нужен TEST_DATABASE_URL)."""

from __future__ import annotations

import json
from pathlib import Path

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from pydantic import SecretStr
from sqlalchemy import Engine, func, inspect, select
from sqlalchemy.engine import URL
from sqlalchemy.orm import Session

from backend import migrate
from backend.app.health import HealthChecker
from backend.core.catalog import CatalogImportError, import_catalog_file
from backend.core.db import make_engine
from backend.core.models import Base, CatalogItem, CatalogVersion
from backend.core.readiness import write_status
from backend.core.settings import FIXTURE_CATALOG, REPO_ROOT, Settings

from .conftest import FakeRedis

pytestmark = pytest.mark.db


def _engine(url: URL) -> Engine:
    return make_engine(url, application_name="ktru-tests", null_pool=True)


def _psycopg_dsn(url: URL) -> str:
    return url.set(drivername="postgresql").render_as_string(hide_password=False)


# Снимок схемы: колонки с типами, ограничения, индексы, представления, триггеры, свои функции, расширения.
_SNAPSHOT_QUERIES = {
    "columns": """
        SELECT c.relname, a.attname, format_type(a.atttypid, a.atttypmod), a.attnotnull,
               pg_get_expr(d.adbin, d.adrelid), a.attgenerated
        FROM pg_attribute a
        JOIN pg_class c ON c.oid = a.attrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        LEFT JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum
        WHERE n.nspname = 'public' AND a.attnum > 0 AND NOT a.attisdropped
          AND c.relkind IN ('r', 'v') AND c.relname <> 'alembic_version'
        ORDER BY 1, 2""",
    "constraints": """
        SELECT conrelid::regclass::text, conname, pg_get_constraintdef(oid) FROM pg_constraint
        WHERE connamespace = 'public'::regnamespace AND conrelid::regclass::text <> 'alembic_version'
        ORDER BY 1, 2""",
    "indexes": """
        SELECT tablename, indexname, indexdef FROM pg_indexes
        WHERE schemaname = 'public' AND tablename <> 'alembic_version' ORDER BY 1, 2""",
    "views": "SELECT viewname, pg_get_viewdef(viewname::regclass, true) FROM pg_views WHERE schemaname = 'public'",
    "triggers": """
        SELECT event_object_table, trigger_name, action_timing, event_manipulation, action_statement
        FROM information_schema.triggers WHERE trigger_schema = 'public' ORDER BY 1, 2, 4""",
    "functions": """
        SELECT p.proname, pg_get_functiondef(p.oid) FROM pg_proc p
        WHERE p.pronamespace = 'public'::regnamespace
          AND NOT EXISTS (SELECT 1 FROM pg_depend d WHERE d.objid = p.oid AND d.deptype = 'e')
        ORDER BY 1""",
    "extensions": "SELECT extname FROM pg_extension ORDER BY 1",
}


def _snapshot(url: URL) -> dict[str, list[tuple]]:
    with psycopg.connect(_psycopg_dsn(url)) as conn:
        return {name: conn.execute(query).fetchall() for name, query in _SNAPSHOT_QUERIES.items()}


def _owner_url(s: Settings) -> URL:
    return s.owner_database_url()


def test_migrate_creates_schema_role_and_catalog(db_settings: Settings) -> None:
    assert migrate.main(db_settings) == 0
    engine = _engine(_owner_url(db_settings))
    with Session(engine) as session:
        versions = session.scalars(select(CatalogVersion)).all()
        assert [(v.status, v.items_count, v.source) for v in versions] == [("ready", 8, "migrate:fake_catalog.jsonl")]
        items = session.scalars(select(CatalogItem)).all()
        assert {i.ktru_code for i in items} == {
            json.loads(line)["ktru_code"]
            for line in (REPO_ROOT / "contracts/fixtures/fake_catalog.jsonl").read_text(encoding="utf-8").splitlines()
        }
        assert all(i.search_text for i in items)
        # tsv строится базой из search_text: полнотекстовый поиск по-русски работает сразу
        found = session.scalar(
            select(func.count())
            .select_from(CatalogItem)
            .where(CatalogItem.tsv.op("@@")(func.plainto_tsquery("russian", "ноутбуки")))
        )
        assert found == 2

    # Повторный запуск ничего не дублирует.
    assert migrate.main(db_settings) == 0
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(CatalogVersion)) == 1
    engine.dispose()


def test_app_role_can_use_data_but_not_change_schema(db_settings: Settings) -> None:
    assert migrate.main(db_settings) == 0
    dsn = _psycopg_dsn(db_settings.app_database_url())
    with psycopg.connect(dsn, autocommit=True) as conn:
        assert conn.execute("SELECT count(*) FROM catalog_items").fetchone() == (8,)
        conn.execute("INSERT INTO eval_runs (split, config) VALUES ('dev', '{}')")
        conn.execute("SELECT count(*) FROM v_item_final")
        for statement in (
            "CREATE TABLE intruder (id int)",
            "DROP TABLE eval_runs",
            "ALTER TABLE jobs ADD COLUMN x int",
            "SELECT * FROM alembic_version",
            "CREATE ROLE intruder",
        ):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute(statement)


def test_app_password_change_is_applied(db_settings: Settings) -> None:
    assert migrate.main(db_settings) == 0
    changed = db_settings.model_copy(update={"app_db_password": SecretStr("new-secret")})
    assert migrate.main(changed) == 0
    with psycopg.connect(_psycopg_dsn(changed.app_database_url())) as conn:
        assert conn.execute("SELECT 1").fetchone() == (1,)
    with pytest.raises(psycopg.OperationalError):
        psycopg.connect(_psycopg_dsn(db_settings.app_database_url())).close()


def test_migrations_match_contract(db_settings: Settings, admin_url: URL) -> None:
    """Схема после alembic upgrade head совпадает с contracts/schema.sql — контракт и миграции не разъехались."""
    assert migrate.main(db_settings) == 0
    contract_db = f"{db_settings.postgres_db}_contract"
    with psycopg.connect(_psycopg_dsn(admin_url), autocommit=True) as admin:
        admin.execute(f'CREATE DATABASE "{contract_db}"')
    try:
        contract_url = _owner_url(db_settings).set(database=contract_db)
        with psycopg.connect(_psycopg_dsn(contract_url), autocommit=True) as conn:
            conn.execute((REPO_ROOT / "contracts" / "schema.sql").read_text(encoding="utf-8"))
        migrated, contract = _snapshot(_owner_url(db_settings)), _snapshot(contract_url)
        for part in _SNAPSHOT_QUERIES:
            assert migrated[part] == contract[part], f"Расхождение со schema.sql: {part}"
    finally:
        with psycopg.connect(_psycopg_dsn(admin_url), autocommit=True) as admin:
            admin.execute(f'DROP DATABASE IF EXISTS "{contract_db}" WITH (FORCE)')


def test_downgrade_and_upgrade_again(db_settings: Settings) -> None:
    assert migrate.main(db_settings) == 0
    engine = _engine(_owner_url(db_settings))
    config = Config(str(migrate.ALEMBIC_INI))
    with engine.begin() as conn:
        config.attributes["connection"] = conn
        command.downgrade(config, "base")
    assert set(inspect(engine).get_table_names()) == {"alembic_version"}
    migrate.upgrade(engine)
    assert "catalog_items" in inspect(engine).get_table_names()
    engine.dispose()


def test_models_match_database(db_settings: Settings) -> None:
    assert migrate.main(db_settings) == 0
    engine = _engine(_owner_url(db_settings))
    inspector = inspect(engine)
    for table in Base.metadata.sorted_tables:
        db_columns = {c["name"]: c["nullable"] for c in inspector.get_columns(table.name)}
        model_columns = {c.name: c.nullable for c in table.columns}
        assert model_columns == db_columns, f"Модель {table.name} не совпадает с таблицей"
    engine.dispose()


def test_catalog_import_rules(db_settings: Settings, tmp_path: Path) -> None:
    assert migrate.main(db_settings) == 0
    row = {"ktru_code": "11.11.11.111-00000001", "okpd2_code": "11.11.11.111", "name": "Стол"}
    path = tmp_path / "catalog.jsonl"
    path.write_text(
        "\n".join(
            json.dumps(r, ensure_ascii=False)
            for r in (
                row,
                {**row, "name": "Стол письменный"},
                {**row, "ktru_code": "11.11.11.111-00000002", "is_enlarged": True},
            )
        )
        + "\n\n",
        encoding="utf-8",
    )
    engine = _engine(_owner_url(db_settings))
    with Session(engine) as session, session.begin():
        version = import_catalog_file(session, path, source="test")
        assert (version.status, version.items_count) == ("ready", 2)
        names = session.scalars(select(CatalogItem.name).where(CatalogItem.version_id == version.id)).all()
        assert sorted(names) == ["Стол", "Стол письменный"]  # повтор кода в файле обновил строку
        enlarged = session.execute(
            select(CatalogItem.ktru_code, CatalogItem.is_enlarged).where(CatalogItem.version_id == version.id)
        ).all()
        assert sorted(enlarged) == [("11.11.11.111-00000001", False), ("11.11.11.111-00000002", True)]

    for bad, message in (
        ("{не json", "Строка 1: некорректный JSON"),
        ('{"ktru_code": "x"}', "Строка 1: не соответствует"),
    ):
        path.write_text(bad + "\n", encoding="utf-8")
        with Session(engine) as session, session.begin(), pytest.raises(CatalogImportError, match=message):
            import_catalog_file(session, path, source="test")
    engine.dispose()


def _catalog_versions(settings: Settings) -> list[tuple[str, int]]:
    engine = _engine(settings.app_database_url())
    with Session(engine) as session:
        rows = session.execute(
            select(CatalogVersion.source, CatalogVersion.items_count).order_by(CatalogVersion.loaded_at)
        ).all()
    engine.dispose()
    return [tuple(r) for r in rows]


def test_real_pipeline_downloads_missing_catalog(
    db_settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "catalog" / "catalog.jsonl"
    calls: list[Path] = []

    def download(target: Path) -> bool:
        calls.append(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(FIXTURE_CATALOG.read_bytes())
        return True

    monkeypatch.setattr(migrate, "download_catalog", download)
    real = db_settings.model_copy(update={"pipeline": "real", "ktru_catalog_path": path})
    assert migrate.main(real) == 0
    assert migrate.main(real) == 0  # файл уже на томе — второй раз не скачиваем
    assert calls == [path]
    assert _catalog_versions(db_settings) == [("migrate:catalog.jsonl", 8)]


def test_catalog_download_failure_falls_back_to_stub(
    db_settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(migrate, "download_catalog", lambda target: False)
    real = db_settings.model_copy(update={"pipeline": "real", "ktru_catalog_path": tmp_path / "catalog.jsonl"})
    assert migrate.main(real) == 0  # система запускается на заглушке, а не падает
    assert _catalog_versions(db_settings) == [("migrate:fake_catalog.jsonl", 8)]


def test_real_pipeline_without_catalog_path_is_config_error(db_settings: Settings) -> None:
    assert migrate.main(db_settings.model_copy(update={"pipeline": "real", "ktru_catalog_path": None})) == 1


def test_health_reads_catalog_from_database(db_settings: Settings, fake_redis: FakeRedis) -> None:
    assert migrate.main(db_settings) == 0
    write_status(fake_redis, {"models": True, "catalog": True, "pipeline": "stub", "error": None}, 30)
    engine = _engine(db_settings.app_database_url())
    health = HealthChecker(engine, fake_redis).check()  # type: ignore[arg-type]
    engine.dispose()
    assert health.ready is True
    assert health.components.catalog.detail.endswith(", 8 позиций")
