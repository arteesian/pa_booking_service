"""Снимок директории: HUID eXpress сотрудника.

Revision ID: 0004_express_huid
Revises: 0003_library
Create Date: 2026-10-06
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_express_huid"
down_revision: str | None = "0003_library"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("directory_employees", sa.Column("express_huid", sa.String(64), nullable=True))
    op.create_index(
        "ix_directory_employees_express_huid", "directory_employees", ["express_huid"]
    )


def downgrade() -> None:
    op.drop_index("ix_directory_employees_express_huid", table_name="directory_employees")
    op.drop_column("directory_employees", "express_huid")
