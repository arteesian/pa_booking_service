"""Пользователь брони и выдачи — HUID eXpress (спека Р-8, §4).

``employee_id`` → ``user_huid`` + снимок ``user_name`` + ``channel`` (откуда пришла
бронь). Существующие строки (до этой миграции бывали только из ЛК) получают HUID и
ФИО из снимка ростера. Строка без HUID в ростере — миграция падает со списком id:
бронь не теряется молча, сначала привязываем учётку eXpress в auth.

Revision ID: 0005_user_huid
Revises: 0004_express_huid
Create Date: 2026-10-07
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005_user_huid"
down_revision: str | None = "0004_express_huid"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

booking_channel = postgresql.ENUM("lk", "express", name="booking_channel", create_type=False)

# HUID в снимке до нормализации синком мог лежать в любом регистре или быть мусором.
# Порядок вычисления условий в WHERE Postgres не гарантирует, поэтому в downgrade
# приведение к uuid спрятано в CASE; в upgrade SET вычисляется только для строк,
# уже прошедших WHERE.
_UUID_RE = "^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"

# Таблица → (старый индекс, его колонки, новый индекс, его колонки, условие индекса).
_TABLES: dict[str, tuple[str, list[str], str, list[str], str | None]] = {
    "appointment_bookings": (
        "ix_appointment_bookings_employee",
        ["employee_id", "status"],
        "ix_appointment_bookings_user_huid",
        ["user_huid", "status"],
        None,
    ),
    "library_loans": (
        "ix_library_loans_employee",
        ["employee_id"],
        "ix_library_loans_user_huid",
        ["user_huid"],
        "returned_at IS NULL",
    ),
}


def _where(cond: str | None) -> sa.TextClause | None:
    return None if cond is None else sa.text(cond)


def _fail_if_null(table: str, column: str, hint: str) -> None:
    ids = (
        op.get_bind()
        .execute(sa.text(f"SELECT id FROM {table} WHERE {column} IS NULL ORDER BY id"))
        .scalars()
        .all()
    )
    if ids:
        raise RuntimeError(f"{table}: у строк {list(ids)} нет {column} — {hint}")


def upgrade() -> None:
    booking_channel.create(op.get_bind())
    for table, (old_ix, _old_cols, new_ix, new_cols, cond) in _TABLES.items():
        op.add_column(table, sa.Column("user_huid", sa.Uuid(), nullable=True))
        op.add_column(table, sa.Column("user_name", sa.String(256), nullable=True))
        op.add_column(table, sa.Column("channel", booking_channel, nullable=True))
        op.execute(
            sa.text(
                f"""
                UPDATE {table} AS t
                SET user_huid = d.express_huid::uuid, user_name = d.full_name, channel = 'lk'
                FROM directory_employees AS d
                WHERE d.employee_id = t.employee_id AND d.express_huid ~ :re
                """
            ).bindparams(re=_UUID_RE)
        )
        _fail_if_null(table, "user_huid", "привяжите учётку eXpress в auth и повторите")
        op.alter_column(table, "user_huid", nullable=False)
        op.alter_column(table, "channel", nullable=False)
        op.drop_index(old_ix, table_name=table)
        op.drop_column(table, "employee_id")
        op.create_index(new_ix, table, new_cols, postgresql_where=_where(cond))


def downgrade() -> None:
    for table, (old_ix, old_cols, new_ix, _new_cols, cond) in _TABLES.items():
        op.add_column(table, sa.Column("employee_id", sa.Uuid(), nullable=True))
        op.execute(
            sa.text(
                f"""
                UPDATE {table} AS t SET employee_id = d.employee_id
                FROM directory_employees AS d
                WHERE CASE WHEN d.express_huid ~ :re THEN d.express_huid::uuid END = t.user_huid
                """
            ).bindparams(re=_UUID_RE)
        )
        _fail_if_null(table, "employee_id", "HUID нет в ростере (не сотрудник ЛК)")
        op.alter_column(table, "employee_id", nullable=False)
        op.drop_index(new_ix, table_name=table)
        op.drop_column(table, "channel")
        op.drop_column(table, "user_name")
        op.drop_column(table, "user_huid")
        op.create_index(old_ix, table, old_cols, postgresql_where=_where(cond))
    booking_channel.drop(op.get_bind())
