"""Записи: слоты специалиста и брони.

Revision ID: 0002_appointments
Revises: 0001_directory
Create Date: 2026-10-03
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_appointments"
down_revision: str | None = "0001_directory"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Значения заморожены здесь, а не импортом из domain: миграция — снимок схемы на момент
# написания и не должна меняться вместе с кодом.
appointment_kind = postgresql.ENUM("psy", "mkr", name="appointment_kind", create_type=False)
appointment_status = postgresql.ENUM(
    "active",
    "cancelled_by_user",
    "cancelled_by_specialist",
    name="appointment_status",
    create_type=False,
)


def upgrade() -> None:
    bind = op.get_bind()
    appointment_kind.create(bind)
    appointment_status.create(bind)

    op.create_table(
        "appointment_slots",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("slot_date", sa.Date(), nullable=False),
        sa.Column("slot_time", sa.Time(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("removed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "uq_appointment_slots_active",
        "appointment_slots",
        ["slot_date", "slot_time"],
        unique=True,
        postgresql_where=sa.text("removed_at IS NULL"),
    )

    op.create_table(
        "appointment_bookings",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "slot_id", sa.BigInteger(), sa.ForeignKey("appointment_slots.id"), nullable=False
        ),
        sa.Column("employee_id", sa.Uuid(), nullable=False),
        sa.Column("kind", appointment_kind, nullable=False),
        sa.Column("status", appointment_status, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "uq_appointment_bookings_active_slot",
        "appointment_bookings",
        ["slot_id"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )
    op.create_index(
        "ix_appointment_bookings_employee", "appointment_bookings", ["employee_id", "status"]
    )


def downgrade() -> None:
    op.drop_table("appointment_bookings")
    op.drop_table("appointment_slots")
    bind = op.get_bind()
    appointment_status.drop(bind)
    appointment_kind.drop(bind)
