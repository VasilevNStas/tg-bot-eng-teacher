"""add content item tags

Revision ID: 8b2e5f7a1c93
Revises: 0f6d4a2c8b71
Create Date: 2026-10-29 12:30:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "8b2e5f7a1c93"
down_revision: str | None = "0f6d4a2c8b71"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "content_item_tags",
        sa.Column("content_item_id", sa.Integer(), nullable=False),
        sa.Column("tag", sa.String(length=64), nullable=False),
        sa.ForeignKeyConstraint(
            ["content_item_id"],
            ["content_items.id"],
            name=op.f("fk_content_item_tags_content_item_id_content_items"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("content_item_id", "tag", name=op.f("pk_content_item_tags")),
    )
    op.create_index(
        op.f("ix_content_item_tags_tag"),
        "content_item_tags",
        ["tag"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_content_item_tags_tag"), table_name="content_item_tags")
    op.drop_table("content_item_tags")
