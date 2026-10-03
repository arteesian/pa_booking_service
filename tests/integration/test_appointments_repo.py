from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, time

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from pa_booking.db import appointments as repo
from pa_booking.db.models import AppointmentBooking, AppointmentSlot
from pa_booking.domain.appointments import BookingStatus, Kind

pytestmark = pytest.mark.db

NOW = datetime(2026, 10, 3, 9, 0, tzinfo=UTC)
EMP = uuid.UUID("11111111-1111-1111-1111-111111111111")


def _slot(s: Session, d: date, t: time = time(16, 0), *, removed: bool = False) -> AppointmentSlot:
    slot = AppointmentSlot(slot_date=d, slot_time=t, removed_at=NOW if removed else None)
    s.add(slot)
    s.flush()
    return slot


def _book(
    s: Session,
    slot: AppointmentSlot,
    status: BookingStatus = BookingStatus.ACTIVE,
    *,
    employee_id: uuid.UUID = EMP,
) -> AppointmentBooking:
    booking = AppointmentBooking(
        slot_id=slot.id, employee_id=employee_id, kind=Kind.PSY, status=status
    )
    s.add(booking)
    s.flush()
    return booking


def test_count_active_in_month_counts_past_and_skips_cancelled(db_session: Session) -> None:
    _book(db_session, _slot(db_session, date(2026, 10, 1)))  # прошедшая — считается
    _book(db_session, _slot(db_session, date(2026, 10, 31)))  # граница месяца
    _book(db_session, _slot(db_session, date(2026, 10, 20)), BookingStatus.CANCELLED_BY_USER)
    _book(db_session, _slot(db_session, date(2026, 10, 21)), BookingStatus.CANCELLED_BY_SPECIALIST)
    _book(db_session, _slot(db_session, date(2026, 11, 1)))  # другой месяц
    _book(db_session, _slot(db_session, date(2026, 10, 22)), employee_id=uuid.uuid4())

    assert repo.count_active_in_month(db_session, EMP, 2026, 10) == 2
    assert repo.count_active_in_month(db_session, EMP, 2026, 11) == 1


def test_add_slots_reports_duplicates_of_live_slots_only(db_session: Session) -> None:
    d = date(2026, 10, 5)
    _slot(db_session, d, time(10, 0))
    _slot(db_session, d, time(11, 0), removed=True)

    added, duplicates = repo.add_slots(
        db_session, d, [time(12, 0), time(10, 0), time(11, 0), time(12, 0)], now=NOW
    )

    assert added == [time(11, 0), time(12, 0)]
    assert duplicates == [time(10, 0)]


def test_free_slots_skip_booked_removed_and_out_of_range(db_session: Session) -> None:
    d = date(2026, 10, 5)
    free = _slot(db_session, d, time(10, 0))
    _book(db_session, _slot(db_session, d, time(11, 0)))
    _slot(db_session, d, time(12, 0), removed=True)
    reopened = _slot(db_session, d, time(13, 0))
    _book(db_session, reopened, BookingStatus.CANCELLED_BY_USER)
    _slot(db_session, date(2026, 10, 6))

    ids = [s.id for s in repo.free_slots(db_session, d, d)]

    assert ids == [free.id, reopened.id]


def test_remove_free_slots_on_keeps_booked_and_other_days(db_session: Session) -> None:
    d = date(2026, 10, 5)
    _slot(db_session, d, time(17, 0))
    _slot(db_session, d, time(16, 0))
    booked = _slot(db_session, d, time(18, 0))
    _book(db_session, booked)
    tomorrow = _slot(db_session, date(2026, 10, 6), time(16, 0))

    assert repo.remove_free_slots_on(db_session, d, now=NOW) == [time(16, 0), time(17, 0)]
    db_session.refresh(booked)
    db_session.refresh(tomorrow)
    assert booked.removed_at is None
    assert tomorrow.removed_at is None
    assert repo.remove_free_slots_on(db_session, d, now=NOW) == []


def test_overview_shows_live_slots_with_active_booking(db_session: Session) -> None:
    d = date(2026, 10, 5)
    booked = _slot(db_session, d, time(10, 0))
    booking = _book(db_session, booked)
    cancelled = _slot(db_session, d, time(11, 0))
    _book(db_session, cancelled, BookingStatus.CANCELLED_BY_USER)
    _slot(db_session, d, time(12, 0), removed=True)

    rows = repo.overview(db_session, d, d)

    assert [(r.slot.id, r.booking.id if r.booking else None) for r in rows] == [
        (booked.id, booking.id),
        (cancelled.id, None),
    ]


def test_my_bookings_filters_status_and_date(db_session: Session) -> None:
    active = _book(db_session, _slot(db_session, date(2026, 10, 5)))
    by_specialist = _book(
        db_session, _slot(db_session, date(2026, 10, 6)), BookingStatus.CANCELLED_BY_SPECIALIST
    )
    _book(db_session, _slot(db_session, date(2026, 10, 7)), BookingStatus.CANCELLED_BY_USER)
    _book(db_session, _slot(db_session, date(2026, 10, 1)))  # раньше since

    rows = repo.my_bookings(
        db_session,
        EMP,
        [BookingStatus.ACTIVE, BookingStatus.CANCELLED_BY_SPECIALIST],
        since=date(2026, 10, 3),
    )

    assert [r.booking.id for r in rows] == [active.id, by_specialist.id]


def test_export_rows_like_bot_plus_statuses(db_session: Session) -> None:
    d = date(2026, 10, 5)
    other = uuid.uuid4()
    _slot(db_session, d, time(9, 0))  # свободный
    _book(db_session, _slot(db_session, d, time(10, 0)))  # занят
    reopened = _slot(db_session, d, time(11, 0))  # отменён и снова свободен
    _book(db_session, reopened, BookingStatus.CANCELLED_BY_USER, employee_id=other)
    removed_booked = _slot(db_session, d, time(12, 0), removed=True)  # удалён специалистом
    _book(db_session, removed_booked, BookingStatus.CANCELLED_BY_SPECIALIST)
    _slot(db_session, d, time(13, 0), removed=True)  # удалён чисткой — не выгружается
    _slot(db_session, date(2026, 11, 2), time(9, 0))  # вне периода

    rows = repo.export_rows(db_session, date(2026, 10, 1), date(2026, 10, 31))

    assert [(r.slot_time, r.employee_id, r.status) for r in rows] == [
        (time(9, 0), None, None),
        (time(10, 0), EMP, BookingStatus.ACTIVE),
        (time(11, 0), other, BookingStatus.CANCELLED_BY_USER),
        (time(11, 0), None, None),
        (time(12, 0), EMP, BookingStatus.CANCELLED_BY_SPECIALIST),
    ]
    assert len(repo.export_rows(db_session, None, None)) == 6


def test_lock_slot_blocks_second_transaction(db_session: Session, pg_engine: Engine) -> None:
    slot = _slot(db_session, date(2026, 10, 5))
    db_session.commit()

    assert repo.lock_slot(db_session, slot.id) is not None
    with Session(pg_engine) as other:
        other.execute(text("SET LOCAL lock_timeout = '200ms'"))
        with pytest.raises(OperationalError, match="lock timeout"):
            repo.lock_slot(other, slot.id)
    db_session.rollback()


def test_lock_slot_missing_returns_none(db_session: Session) -> None:
    assert repo.lock_slot(db_session, 999_999) is None


def test_lock_employee_blocks_same_employee_only(db_session: Session, pg_engine: Engine) -> None:
    repo.lock_employee(db_session, EMP)
    key = text("SELECT pg_try_advisory_xact_lock(hashtextextended(:key, 0))")
    with Session(pg_engine) as other:
        assert other.scalar(key, {"key": f"appointments:{EMP}"}) is False
        assert other.scalar(key, {"key": f"appointments:{uuid.uuid4()}"}) is True
    db_session.rollback()


def test_lock_slot_rereads_rows_loaded_before_lock(db_session: Session, pg_engine: Engine) -> None:
    """Отмена грузит бронь и слот до блокировки; после неё нужны свежие данные."""
    slot = _slot(db_session, date(2026, 10, 5))
    booking = _book(db_session, slot)
    db_session.commit()
    assert repo.find_booking(db_session, booking.id) is not None  # слот уже в identity map
    db_session.commit()

    with Session(pg_engine) as other:  # параллельно специалист удаляет слот
        other.get(AppointmentSlot, slot.id).removed_at = NOW  # type: ignore[union-attr]
        other.get(AppointmentBooking, booking.id).status = (  # type: ignore[union-attr]
            BookingStatus.CANCELLED_BY_SPECIALIST
        )
        other.commit()

    locked = repo.lock_slot(db_session, slot.id)
    assert locked is not None
    assert locked.slot.removed_at is not None
    assert locked.booking is None
