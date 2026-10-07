from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from pa_booking.db.models import LibraryBook, LibraryLoan
from pa_booking.domain.identity import Channel

pytestmark = pytest.mark.db


def _book(s: Session) -> LibraryBook:
    book = LibraryBook(genre="Роман", author="Булгаков", title="Мастер", description="—")
    s.add(book)
    s.flush()
    return book


def _loan(s: Session, book: LibraryBook, *, returned: bool = False) -> None:
    s.add(
        LibraryLoan(
            book_id=book.id,
            user_huid=uuid.uuid4(),
            channel=Channel.LK,
            starts_on=date(2026, 10, 5),
            due_on=date(2026, 10, 12),
            returned_at=datetime.now(UTC) if returned else None,
        )
    )
    s.flush()


def test_second_open_loan_of_book_rejected(db_session: Session) -> None:
    book = _book(db_session)
    _loan(db_session, book)
    with pytest.raises(IntegrityError):
        _loan(db_session, book)


def test_returned_loans_do_not_block_new_one(db_session: Session) -> None:
    book = _book(db_session)
    _loan(db_session, book, returned=True)
    _loan(db_session, book, returned=True)
    _loan(db_session, book)
