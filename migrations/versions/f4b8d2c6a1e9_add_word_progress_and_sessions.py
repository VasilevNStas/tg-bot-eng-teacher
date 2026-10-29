"""add word progress and word-card session fields

Revision ID: f4b8d2c6a1e9
Revises: e2a4f6b8c9d1
Create Date: 2026-10-29 16:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f4b8d2c6a1e9"
down_revision: str | None = "e2a4f6b8c9d1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("exercise_sessions", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "kind",
                sa.Enum(
                    "DRILL",
                    "WORDS",
                    name="exercisesessionkind",
                    native_enum=False,
                    length=32,
                    validate_strings=True,
                ),
                nullable=False,
                server_default="DRILL",
            )
        )
        batch_op.add_column(
            sa.Column(
                "meta",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'{}'"),
            )
        )

    with op.batch_alter_table("exercise_attempts", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "direction",
                sa.Enum(
                    "EN_RU",
                    "RU_EN",
                    name="worddirection",
                    native_enum=False,
                    length=32,
                    validate_strings=True,
                ),
                nullable=True,
            )
        )

    op.create_table(
        "word_progress",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("content_item_id", sa.Integer(), nullable=False),
        sa.Column("shown_count", sa.Integer(), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("correct_count", sa.Integer(), nullable=False),
        sa.Column("partial_count", sa.Integer(), nullable=False),
        sa.Column("wrong_count", sa.Integer(), nullable=False),
        sa.Column("streak", sa.Integer(), nullable=False),
        sa.Column("best_streak", sa.Integer(), nullable=False),
        sa.Column("last_shown_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_attempted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["content_item_id"], ["content_items.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "content_item_id", name="uq_word_progress_user_item"),
    )
    op.create_index("ix_word_progress_user_id", "word_progress", ["user_id"], unique=False)
    op.create_index(
        "ix_word_progress_content_item_id",
        "word_progress",
        ["content_item_id"],
        unique=False,
    )
    op.create_index(
        "ix_word_progress_user_due_at",
        "word_progress",
        ["user_id", "next_due_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_word_progress_user_due_at", table_name="word_progress")
    op.drop_index("ix_word_progress_content_item_id", table_name="word_progress")
    op.drop_index("ix_word_progress_user_id", table_name="word_progress")
    op.drop_table("word_progress")

    with op.batch_alter_table("exercise_attempts", schema=None) as batch_op:
        batch_op.drop_column("direction")

    with op.batch_alter_table("exercise_sessions", schema=None) as batch_op:
        batch_op.drop_column("meta")
        batch_op.drop_column("kind")
