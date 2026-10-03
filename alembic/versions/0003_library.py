"""Библиотека: каталог книг и выдачи.

Revision ID: 0003_library
Revises: 0002_appointments
Create Date: 2026-10-03
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_library"
down_revision: str | None = "0002_appointments"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "library_books",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("genre", sa.String(255), nullable=False),
        sa.Column("author", sa.String(255), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("removed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "library_loans",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("book_id", sa.BigInteger(), sa.ForeignKey("library_books.id"), nullable=False),
        sa.Column("employee_id", sa.Uuid(), nullable=False),
        sa.Column("starts_on", sa.Date(), nullable=False),
        sa.Column("due_on", sa.Date(), nullable=False),
        sa.Column("returned_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "returned_by_librarian",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "uq_library_loans_open",
        "library_loans",
        ["book_id"],
        unique=True,
        postgresql_where=sa.text("returned_at IS NULL"),
    )
    op.create_index(
        "ix_library_loans_employee",
        "library_loans",
        ["employee_id"],
        postgresql_where=sa.text("returned_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_table("library_loans")
    op.drop_table("library_books")
