from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, time

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from pa_booking.db.models import (
    AppointmentBooking,
    AppointmentSlot,
    LibraryBook,
    LibraryLoan,
)
from pa_booking.domain.appointments import BookingStatus, Kind
from pa_booking.domain.identity import Channel
from pa_booking.migrate.bots import (
    BookRow,
    SlotPlan,
    SlotRow,
    apply_books,
    apply_slots,
    plan_books,
    plan_slots,
)
from tests.integration.conftest import add_roster

pytestmark = pytest.mark.db

NOW = datetime(2026, 10, 6, 9, 0, tzinfo=UTC)
HUID = uuid.UUID("aaaaaaaa-0000-0000-0000-000000000001")
STRANGER = uuid.UUID("bbbbbbbb-0000-0000-0000-000000000002")  # не из КЦ: нет в ростере


def _plan() -> SlotPlan:
    rows = [
        SlotRow("07.10.2026", "10:00", None, None, None),
        SlotRow("07.10.2026", "11:00", str(HUID), None, "мкр"),
        SlotRow("07.10.2026", "12:00", str(STRANGER), None, "психолог"),
        SlotRow("08.10.2026", "10:00", None, None, None),
        SlotRow("08.10.2026", "11:00", None, 123, None),  # Telegram — не переносится
    ]
    return plan_slots(rows, today=date(2026, 10, 6))


def _count(session: Session, model: type) -> int:
    return session.scalar(select(func.count()).select_from(model)) or 0


def test_apply_slots_creates_slots_and_bookings_by_huid_idempotently(db_session: Session) -> None:
    add_roster(db_session, HUID, "Иванова Анна")
    plan = _plan()
    first = apply_slots(db_session, plan, now=NOW)
    db_session.commit()

    assert (first.added, first.skipped, first.bookings) == (4, 0, 2)
    rows = db_session.execute(
        select(
            AppointmentSlot.slot_time,
            AppointmentBooking.user_huid,
            AppointmentBooking.user_name,
            AppointmentBooking.channel,
            AppointmentBooking.kind,
            AppointmentBooking.status,
        )
        .join(AppointmentSlot, AppointmentSlot.id == AppointmentBooking.slot_id)
        .order_by(AppointmentSlot.slot_time)
    ).all()
    assert [tuple(r) for r in rows] == [
        (time(11, 0), HUID, "Иванова Анна", Channel.EXPRESS, Kind.MKR, BookingStatus.ACTIVE),
        (time(12, 0), STRANGER, None, Channel.EXPRESS, Kind.PSY, BookingStatus.ACTIVE),
    ]

    second = apply_slots(db_session, plan, now=NOW)
    db_session.commit()

    assert (second.added, second.skipped, second.bookings) == (0, 4, 0)
    assert _count(db_session, AppointmentSlot) == 4
    assert _count(db_session, AppointmentBooking) == 2


def test_apply_slots_refuses_booking_without_kind(db_session: Session) -> None:
    plan = plan_slots(
        [SlotRow("07.10.2026", "11:00", str(HUID), None, "к Анне")], today=date(2026, 10, 6)
    )
    with pytest.raises(ValueError, match="--kind"):
        apply_slots(db_session, plan, now=NOW)
    assert _count(db_session, AppointmentSlot) == 0


BOOKS = [
    BookRow(" Роман ", "Булгаков", "Мастер и Маргарита", "—"),
    BookRow(
        "Поэзия",
        "Пушкин",
        "Онегин",
        "—",
        huid=str(HUID),
        start=date(2026, 10, 1),
        end=date(2026, 10, 8),
    ),
]


def test_apply_books_once_with_loans(db_session: Session) -> None:
    add_roster(db_session, HUID, "Иванова Анна")
    result = apply_books(db_session, plan_books(BOOKS), now=NOW)
    db_session.commit()

    assert (result.books, result.loans) == (2, 1)
    assert (
        db_session.scalar(select(LibraryBook.genre).where(LibraryBook.author == "Булгаков"))
        == "Роман"
    )
    loan = db_session.execute(
        select(
            LibraryBook.title, LibraryLoan.user_huid, LibraryLoan.user_name, LibraryLoan.due_on
        ).join(LibraryBook, LibraryBook.id == LibraryLoan.book_id)
    ).one()
    assert tuple(loan) == ("Онегин", HUID, "Иванова Анна", date(2026, 10, 8))

    with pytest.raises(ValueError, match="не пуст"):
        apply_books(db_session, plan_books(BOOKS), now=NOW)


def test_replace_reloads_catalog_without_loans(db_session: Session) -> None:
    apply_books(db_session, plan_books(BOOKS[:1]), now=NOW)
    db_session.commit()

    result = apply_books(db_session, plan_books(BOOKS[1:]), now=NOW, replace=True)
    db_session.commit()

    assert (result.books, result.loans) == (1, 1)
    assert db_session.scalars(select(LibraryBook.title)).all() == ["Онегин"]


def test_replace_refuses_when_service_has_loans(db_session: Session) -> None:
    apply_books(db_session, plan_books(BOOKS), now=NOW)  # одна выдача
    db_session.commit()

    with pytest.raises(ValueError, match="есть выдачи"):
        apply_books(db_session, plan_books(BOOKS), now=NOW, replace=True)
    db_session.rollback()
    assert _count(db_session, LibraryBook) == 2
