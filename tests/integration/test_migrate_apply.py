from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, time

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from pa_booking.db.models import AppointmentBooking, AppointmentSlot, LibraryBook
from pa_booking.domain.appointments import BookingStatus, Kind
from pa_booking.migrate.bots import (
    Assignment,
    BookRow,
    SlotPlan,
    SlotRow,
    apply_books,
    apply_slots,
    plan_slots,
)

pytestmark = pytest.mark.db

NOW = datetime(2026, 10, 6, 9, 0, tzinfo=UTC)
HUID = "aaaaaaaa-0000-0000-0000-000000000001"
EMP = uuid.UUID("11111111-1111-1111-1111-111111111111")


def _plan() -> SlotPlan:
    rows = [
        SlotRow("07.10.2026", "10:00", None, None, None),
        SlotRow("07.10.2026", "11:00", HUID, None, "мкр"),
        SlotRow("08.10.2026", "10:00", None, None, None),
    ]
    return plan_slots(rows, today=date(2026, 10, 6), assignments=[Assignment(HUID, EMP, Kind.MKR)])


def _count(session: Session, model: type) -> int:
    return session.scalar(select(func.count()).select_from(model)) or 0


def test_apply_slots_creates_slots_and_booking_and_is_idempotent(db_session: Session) -> None:
    plan = _plan()
    first = apply_slots(db_session, plan, now=NOW)
    db_session.commit()

    assert (first.added, first.skipped, first.bookings) == (3, 0, 1)
    booking = db_session.scalars(select(AppointmentBooking)).one()
    slot = db_session.get(AppointmentSlot, booking.slot_id)
    assert slot is not None
    assert (slot.slot_date, slot.slot_time) == (date(2026, 10, 7), time(11, 0))
    assert (booking.employee_id, booking.kind, booking.status) == (
        EMP,
        Kind.MKR,
        BookingStatus.ACTIVE,
    )

    second = apply_slots(db_session, plan, now=NOW)
    db_session.commit()

    assert (second.added, second.skipped, second.bookings) == (0, 3, 0)
    assert _count(db_session, AppointmentSlot) == 3
    assert _count(db_session, AppointmentBooking) == 1


def test_apply_slots_refuses_unassigned_booking(db_session: Session) -> None:
    plan = plan_slots(
        [SlotRow("07.10.2026", "11:00", HUID, None, None)],
        today=date(2026, 10, 6),
        assignments=[],
    )
    with pytest.raises(ValueError, match="без --assign"):
        apply_slots(db_session, plan, now=NOW)
    assert _count(db_session, AppointmentSlot) == 0


def test_apply_books_once(db_session: Session) -> None:
    rows = [
        BookRow(" Роман ", "Булгаков", "Мастер и Маргарита", "—"),
        BookRow("Поэзия", "Пушкин", "Онегин", "—"),
    ]

    assert apply_books(db_session, rows, now=NOW) == 2
    db_session.commit()
    assert (
        db_session.scalar(select(LibraryBook.genre).where(LibraryBook.author == "Булгаков"))
        == "Роман"
    )

    with pytest.raises(ValueError, match="не пуст"):
        apply_books(db_session, rows, now=NOW)
