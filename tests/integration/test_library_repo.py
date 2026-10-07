from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from pa_booking.db import library as repo
from pa_booking.db.models import LibraryBook, LibraryLoan
from pa_booking.domain.identity import Channel

pytestmark = pytest.mark.db

EMP = uuid.UUID("11111111-1111-1111-1111-111111111111")
TODAY = date(2026, 10, 5)


def _book(s: Session, title: str, genre: str = "Роман", *, removed: bool = False) -> LibraryBook:
    book = LibraryBook(
        genre=genre,
        author="Автор",
        title=title,
        description="—",
        removed_at=datetime.now(UTC) if removed else None,
    )
    s.add(book)
    s.flush()
    return book


def _loan(
    s: Session,
    book: LibraryBook,
    due_on: date = date(2026, 10, 12),
    *,
    returned_at: datetime | None = None,
    user_huid: uuid.UUID = EMP,
) -> LibraryLoan:
    loan = LibraryLoan(
        book_id=book.id,
        user_huid=user_huid,
        channel=Channel.EXPRESS,
        starts_on=due_on.replace(day=1),
        due_on=due_on,
        returned_at=returned_at,
    )
    s.add(loan)
    s.flush()
    return loan


def test_genres_skip_removed_books_and_duplicates(db_session: Session) -> None:
    _book(db_session, "А", "Роман")
    _book(db_session, "Б", "Роман")
    _book(db_session, "В", "Детектив")
    _book(db_session, "Г", "Поэзия", removed=True)

    assert repo.genres(db_session) == ["Детектив", "Роман"]


def test_catalog_free_first_then_title(db_session: Session) -> None:
    taken = _book(db_session, "Альфа")
    _loan(db_session, taken)
    returned = _book(db_session, "Гамма")
    _loan(db_session, returned, returned_at=datetime.now(UTC))
    free = _book(db_session, "Бета")
    _book(db_session, "Дельта", "Детектив")
    _book(db_session, "Ета", removed=True)

    rows = repo.catalog(db_session, "Роман")

    assert [(r.book.title, r.on_loan) for r in rows] == [
        ("Бета", False),
        ("Гамма", False),
        ("Альфа", True),
    ]
    assert free.id in {r.book.id for r in rows}
    assert len(repo.catalog(db_session, None)) == 4


def test_open_loans_split_by_due_date(db_session: Session) -> None:
    due_today = _loan(db_session, _book(db_session, "А"), TODAY)
    overdue = _loan(db_session, _book(db_session, "Б"), date(2026, 10, 4))
    _loan(db_session, _book(db_session, "В"), date(2026, 10, 1), returned_at=datetime.now(UTC))

    active = repo.open_loans(db_session, overdue=False, today=TODAY)
    late = repo.open_loans(db_session, overdue=True, today=TODAY)

    assert [r.loan.id for r in active] == [due_today.id]
    assert [r.loan.id for r in late] == [overdue.id]


def test_my_loans_include_overdue_exclude_returned_and_others(db_session: Session) -> None:
    overdue = _loan(db_session, _book(db_session, "А"), date(2026, 10, 1))
    current = _loan(db_session, _book(db_session, "Б"), date(2026, 10, 12))
    _loan(db_session, _book(db_session, "В"), returned_at=datetime.now(UTC))
    _loan(db_session, _book(db_session, "Г"), user_huid=uuid.uuid4())

    assert [r.loan.id for r in repo.my_loans(db_session, EMP)] == [overdue.id, current.id]


def test_history_order_like_bot(db_session: Session) -> None:
    early = datetime(2026, 9, 1, tzinfo=UTC)
    late = datetime(2026, 9, 20, tzinfo=UTC)
    returned_early = _loan(db_session, _book(db_session, "А"), returned_at=early)
    open_loan = _loan(db_session, _book(db_session, "Б"))
    returned_late = _loan(db_session, _book(db_session, "В"), returned_at=late)

    assert [r.loan.id for r in repo.history(db_session)] == [
        returned_late.id,
        returned_early.id,
        open_loan.id,
    ]


def test_lock_loan_rereads_row_changed_elsewhere(db_session: Session, pg_engine: Engine) -> None:
    loan = _loan(db_session, _book(db_session, "А"))
    db_session.commit()
    assert repo.lock_loan(db_session, loan.id) is not None
    db_session.commit()

    with Session(pg_engine) as other:
        row = other.get(LibraryLoan, loan.id)
        assert row is not None
        row.returned_at = datetime.now(UTC)
        other.commit()

    locked = repo.lock_loan(db_session, loan.id)
    assert locked is not None
    assert locked.loan.returned_at is not None


def test_lock_book_reports_open_loan(db_session: Session) -> None:
    book = _book(db_session, "А")
    assert repo.lock_book(db_session, book.id) == repo.BookWithLoan(book, None)
    loan = _loan(db_session, book)
    locked = repo.lock_book(db_session, book.id)
    assert locked is not None
    assert locked.loan is loan
    assert repo.lock_book(db_session, 999_999) is None
