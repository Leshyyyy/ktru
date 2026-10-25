"""Начальная схема — замороженная копия contracts/schema.sql (sql/0001_initial.sql).

Копия, а не ссылка на контракт: применённая ревизия не должна меняться, когда меняется контракт.
Следующие изменения схемы — новыми ревизиями.

Revision ID: 0001
Revises:
Create Date: 2026-10-26
"""

from __future__ import annotations

from pathlib import Path

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

SQL_FILE = Path(__file__).resolve().parents[1] / "sql" / "0001_initial.sql"


def upgrade() -> None:
    # Файл целиком одним запросом: psycopg без параметров шлёт его простым протоколом, поэтому
    # несколько команд подряд и тело функции в $$ ... $$ выполняются как есть.
    op.get_bind().exec_driver_sql(SQL_FILE.read_text(encoding="utf-8"))


def downgrade() -> None:
    # Расширения vector и pg_trgm не удаляем: ими могут пользоваться и другие объекты базы.
    op.execute("DROP VIEW IF EXISTS v_item_final")
    op.execute(
        "DROP TABLE IF EXISTS eval_runs, eval_samples, confirmed_examples, decisions, predictions, items, jobs, "
        "file_layouts, source_files, catalog_items, catalog_versions"
    )
    op.execute("DROP FUNCTION IF EXISTS set_updated_at()")
