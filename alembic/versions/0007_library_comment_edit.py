"""Библиотека: правка своего комментария — пометка ``edited_at``.

Revision ID: 0007_library_comment_edit
Revises: 0006_library_social
Create Date: 2026-10-08
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_library_comment_edit"
down_revision: str | None = "0006_library_social"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "library_comments", sa.Column("edited_at", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("library_comments", "edited_at")
