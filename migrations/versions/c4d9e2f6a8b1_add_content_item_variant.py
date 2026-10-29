"""add content item variant lineage

Revision ID: c4d9e2f6a8b1
Revises: 8b2e5f7a1c93
Create Date: 2026-10-29 13:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c4d9e2f6a8b1"
down_revision: str | None = "8b2e5f7a1c93"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("content_items", schema=None) as batch_op:
        batch_op.add_column(sa.Column("variant_of_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            "fk_content_items_variant_of_id_content_items",
            "content_items",
            ["variant_of_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_index(
            batch_op.f("ix_content_items_variant_of_id"),
            ["variant_of_id"],
            unique=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("content_items", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_content_items_variant_of_id"))
        batch_op.drop_constraint(
            "fk_content_items_variant_of_id_content_items",
            type_="foreignkey",
        )
        batch_op.drop_column("variant_of_id")
