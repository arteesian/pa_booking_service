from __future__ import annotations

import uuid
from datetime import date, datetime, time

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from pa_booking.db.models import AppointmentBooking, AppointmentSlot
from pa_booking.domain.appointments import MSK, BookingStatus, Kind, text_cleanup
from pa_booking.domain.identity import Channel
from pa_booking.notify.botx import FakeNotifier
from pa_booking.workers.appointments_cleanup import run_cleanup

pytestmark = pytest.mark.db

TODAY = date(2026, 10, 5)


def _slot(db: Session, t: time, d: date = TODAY) -> AppointmentSlot:
    slot = AppointmentSlot(slot_date=d, slot_time=t)
    db.add(slot)
    db.flush()
    return slot


def _live_times(db: Session) -> list[tuple[date, time]]:
    rows = db.execute(
        select(AppointmentSlot.slot_date, AppointmentSlot.slot_time)
        .where(AppointmentSlot.removed_at.is_(None))
        .order_by(AppointmentSlot.slot_date, AppointmentSlot.slot_time)
    ).all()
    return [(d, t) for d, t in rows]


def test_cleanup_before_14_does_nothing(db_session: Session) -> None:
    _slot(db_session, time(16, 0))
    db_session.commit()
    notifier = FakeNotifier()

    removed = run_cleanup(db_session, notifier, now=datetime(2026, 10, 5, 13, 59, tzinfo=MSK))

    assert removed == []
    assert _live_times(db_session) == [(TODAY, time(16, 0))]
    assert notifier.sent == []


def test_cleanup_removes_only_free_slots_of_today(db_session: Session) -> None:
    _slot(db_session, time(17, 0))
    _slot(db_session, time(16, 0))
    booked = _slot(db_session, time(18, 0))
    db_session.add(
        AppointmentBooking(
            slot_id=booked.id,
            user_huid=uuid.uuid4(),
            channel=Channel.EXPRESS,
            kind=Kind.PSY,
            status=BookingStatus.ACTIVE,
        )
    )
    _slot(db_session, time(16, 0), date(2026, 10, 6))
    db_session.commit()
    notifier = FakeNotifier()

    removed = run_cleanup(db_session, notifier, now=datetime(2026, 10, 5, 14, 0, tzinfo=MSK))

    assert removed == [time(16, 0), time(17, 0)]
    assert _live_times(db_session) == [(TODAY, time(18, 0)), (date(2026, 10, 6), time(16, 0))]
    assert notifier.sent == [text_cleanup([time(16, 0), time(17, 0)])]


def test_cleanup_with_nothing_to_remove_sends_nothing(db_session: Session) -> None:
    notifier = FakeNotifier()
    assert run_cleanup(db_session, notifier, now=datetime(2026, 10, 5, 15, 0, tzinfo=MSK)) == []
    assert notifier.sent == []
