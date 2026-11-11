"""Дополнительные задачи финала: комплект файлов и дозаполнение характеристик.

- batches и jobs.batch_id — несколько файлов, обработанных вместе (POST /batches);
- item_characteristic_values — значения характеристик, введённые пользователем (PUT /items/{id}/characteristics).

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-11
"""

from __future__ import annotations

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE batches (
            id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            name        text,
            created_at  timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("ALTER TABLE jobs ADD COLUMN batch_id uuid REFERENCES batches(id)")
    op.execute("CREATE INDEX jobs_batch ON jobs (batch_id) WHERE batch_id IS NOT NULL")
    op.execute(
        """
        CREATE TABLE item_characteristic_values (
            id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            item_id     uuid        NOT NULL REFERENCES items(id) ON DELETE CASCADE,
            ktru_code   text        NOT NULL,
            name        text        NOT NULL,
            value       text,
            created_at  timestamptz NOT NULL DEFAULT clock_timestamp()
        )
        """
    )
    op.execute(
        "CREATE INDEX item_characteristic_values_item"
        " ON item_characteristic_values (item_id, ktru_code, name, created_at DESC)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE item_characteristic_values")
    op.execute("DROP INDEX jobs_batch")
    op.execute("ALTER TABLE jobs DROP COLUMN batch_id")
    op.execute("DROP TABLE batches")
