"""add topic_items m2m

Revision ID: 0f6d4a2c8b71
Revises: c40c840acafb
Create Date: 2026-10-29 12:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0f6d4a2c8b71"
down_revision: str | None = "c40c840acafb"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "topic_items",
        sa.Column("topic_id", sa.Integer(), nullable=False),
        sa.Column("content_item_id", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["content_item_id"],
            ["content_items.id"],
            name=op.f("fk_topic_items_content_item_id_content_items"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["topic_id"],
            ["topics.id"],
            name=op.f("fk_topic_items_topic_id_topics"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("topic_id", "content_item_id", name=op.f("pk_topic_items")),
    )
    op.create_index(
        op.f("ix_topic_items_content_item_id"),
        "topic_items",
        ["content_item_id"],
        unique=False,
    )

    op.execute(
        "INSERT INTO topic_items (topic_id, content_item_id, position) "
        "SELECT topic_id, id, 0 FROM content_items WHERE topic_id IS NOT NULL"
    )

    with op.batch_alter_table("content_items", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_content_items_topic_id"))
        batch_op.drop_column("topic_id")


def downgrade() -> None:
    with op.batch_alter_table("content_items", schema=None) as batch_op:
        batch_op.add_column(sa.Column("topic_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            "fk_content_items_topic_id_topics",
            "topics",
            ["topic_id"],
            ["id"],
            ondelete="CASCADE",
        )
        batch_op.create_index(
            batch_op.f("ix_content_items_topic_id"),
            ["topic_id"],
            unique=False,
        )

    op.execute(
        "UPDATE content_items SET topic_id = ("
        "SELECT ti.topic_id FROM topic_items AS ti "
        "WHERE ti.content_item_id = content_items.id "
        "ORDER BY ti.position, ti.topic_id LIMIT 1)"
    )
    op.execute(
        "UPDATE content_items SET topic_id = (SELECT MIN(id) FROM topics) WHERE topic_id IS NULL"
    )

    with op.batch_alter_table("content_items", schema=None) as batch_op:
        batch_op.alter_column(
            "topic_id",
            existing_type=sa.Integer(),
            nullable=False,
        )

    op.drop_table("topic_items")
