"""Библиотека: оценки и обсуждения книг.

Тип ``booking_channel`` создан в 0005 — здесь переиспользуется (``create_type=False``)
и при откате не удаляется.

Revision ID: 0006_library_social
Revises: 0005_user_huid
Create Date: 2026-10-08
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006_library_social"
down_revision: str | None = "0005_user_huid"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

booking_channel = postgresql.ENUM("lk", "express", name="booking_channel", create_type=False)


def upgrade() -> None:
    op.create_table(
        "library_ratings",
        sa.Column("book_id", sa.BigInteger(), sa.ForeignKey("library_books.id"), primary_key=True),
        sa.Column("user_huid", sa.Uuid(), primary_key=True),
        sa.Column("user_name", sa.String(256), nullable=True),
        sa.Column("channel", booking_channel, nullable=False),
        sa.Column("score", sa.SmallInteger(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("score BETWEEN 1 AND 5", name="ck_library_ratings_score"),
    )
    op.create_table(
        "library_comments",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("book_id", sa.BigInteger(), sa.ForeignKey("library_books.id"), nullable=False),
        sa.Column("user_huid", sa.Uuid(), nullable=False),
        sa.Column("user_name", sa.String(256), nullable=True),
        sa.Column("channel", booking_channel, nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("removed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "removed_by_librarian",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.create_index(
        "ix_library_comments_book",
        "library_comments",
        ["book_id", "created_at"],
        postgresql_where=sa.text("removed_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_table("library_comments")
    op.drop_table("library_ratings")
