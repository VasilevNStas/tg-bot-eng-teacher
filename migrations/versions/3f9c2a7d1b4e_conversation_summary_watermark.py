"""conversation summary watermark

Revision ID: 3f9c2a7d1b4e
Revises: b885e76081fc
Create Date: 2026-11-06 12:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "3f9c2a7d1b4e"
down_revision: str | None = "b885e76081fc"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("conversations", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("summarized_until_message_id", sa.BigInteger(), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("conversations", schema=None) as batch_op:
        batch_op.drop_column("summarized_until_message_id")
