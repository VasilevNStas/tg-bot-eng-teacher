"""add user quiet hours

Revision ID: e2a4f6b8c9d1
Revises: c4d9e2f6a8b1
Create Date: 2026-10-29 15:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e2a4f6b8c9d1"
down_revision: str | None = "c4d9e2f6a8b1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.add_column(sa.Column("quiet_hours_start", sa.String(length=5), nullable=True))
        batch_op.add_column(sa.Column("quiet_hours_end", sa.String(length=5), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.drop_column("quiet_hours_end")
        batch_op.drop_column("quiet_hours_start")
