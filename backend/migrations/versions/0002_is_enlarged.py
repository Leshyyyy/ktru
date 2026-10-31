"""Укрупнённые позиции каталога и допустимые альтернативы в разметке.

- catalog_items.is_enlarged — укрупнённая позиция (is_template в выгрузке ЕИС): ответом и в поиске не выдаётся;
- eval_samples.alt_valid_codes — коды, которые разметчик тоже признал верными (метрика top1_any_valid).

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-29
"""

from __future__ import annotations

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE catalog_items ADD COLUMN is_enlarged boolean NOT NULL DEFAULT false")
    op.execute("ALTER TABLE eval_samples ADD COLUMN alt_valid_codes text[]")


def downgrade() -> None:
    op.execute("ALTER TABLE eval_samples DROP COLUMN alt_valid_codes")
    op.execute("ALTER TABLE catalog_items DROP COLUMN is_enlarged")
