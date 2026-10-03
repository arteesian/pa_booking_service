"""Снимок директории: ФИО сотрудников и состояние синка.

Revision ID: 0001_directory
Revises:
Create Date: 2026-10-03
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001_directory"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "directory_employees",
        sa.Column("employee_id", sa.Uuid(), primary_key=True),
        sa.Column("full_name", sa.String(256), nullable=False),
        sa.Column("dismissed", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_table(
        "directory_sync_state",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=False),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("employees_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.CheckConstraint("id = 1", name="ck_directory_sync_state_singleton"),
    )


def downgrade() -> None:
    op.drop_table("directory_sync_state")
    op.drop_table("directory_employees")
