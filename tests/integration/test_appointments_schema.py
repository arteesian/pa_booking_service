from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, time

import pytest
from sqlalchemy import Engine, inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from pa_booking.db.models import AppointmentBooking, AppointmentSlot
from pa_booking.domain.appointments import BookingStatus, Kind

pytestmark = pytest.mark.db

D = date(2026, 10, 5)
T = time(16, 0)


def _slot(session: Session, *, removed: bool = False) -> AppointmentSlot:
    slot = AppointmentSlot(
        slot_date=D, slot_time=T, removed_at=datetime.now(UTC) if removed else None
    )
    session.add(slot)
    session.flush()
    return slot


def _booking(session: Session, slot: AppointmentSlot, status: BookingStatus) -> None:
    session.add(
        AppointmentBooking(slot_id=slot.id, employee_id=uuid.uuid4(), kind=Kind.PSY, status=status)
    )
    session.flush()


def test_second_live_slot_same_datetime_rejected(db_session: Session) -> None:
    _slot(db_session)
    with pytest.raises(IntegrityError):
        _slot(db_session)


def test_slot_can_be_readded_after_removal(db_session: Session) -> None:
    _slot(db_session, removed=True)
    _slot(db_session)
    _slot(db_session, removed=True)


def test_second_active_booking_on_slot_rejected(db_session: Session) -> None:
    slot = _slot(db_session)
    _booking(db_session, slot, BookingStatus.ACTIVE)
    with pytest.raises(IntegrityError):
        _booking(db_session, slot, BookingStatus.ACTIVE)


def test_cancelled_and_active_booking_share_slot(db_session: Session) -> None:
    slot = _slot(db_session)
    _booking(db_session, slot, BookingStatus.CANCELLED_BY_USER)
    _booking(db_session, slot, BookingStatus.ACTIVE)


def test_enums_store_values_not_member_names(pg_engine: Engine) -> None:
    enums = {e["name"]: e["labels"] for e in inspect(pg_engine).get_enums()}
    assert enums["appointment_kind"] == ["psy", "mkr"]
    assert enums["appointment_status"] == [
        "active",
        "cancelled_by_user",
        "cancelled_by_specialist",
    ]
