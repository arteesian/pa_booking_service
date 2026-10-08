"""Персистентность библиотеки: каталог, выдачи, оценки и обсуждения.

Правила — в ``domain.library``; здесь чтение/запись и блокировки, коммит — у
вызывающего. Бронь и мягкое удаление книги берут строку книги ``FOR UPDATE``,
продление и возврат — строку выдачи. ``populate_existing`` — по той же причине, что в
``db.appointments.lock_slot``: заблокированные строки перечитываются из БД.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import and_, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from pa_booking.db.models import LibraryBook, LibraryComment, LibraryLoan, LibraryRating
from pa_booking.domain.identity import Channel


@dataclass(frozen=True)
class BookWithLoan:
    book: LibraryBook
    loan: LibraryLoan | None  # только невозвращённая выдача

    @property
    def on_loan(self) -> bool:
        return self.loan is not None


@dataclass(frozen=True)
class LoanWithBook:
    loan: LibraryLoan
    book: LibraryBook


def genres(session: Session) -> list[str]:
    """Жанры неудалённых книг, по алфавиту (бот: ``SELECT DISTINCT genre``)."""
    return list(
        session.scalars(
            select(LibraryBook.genre)
            .where(LibraryBook.removed_at.is_(None))
            .distinct()
            .order_by(LibraryBook.genre)
        )
    )


def catalog(session: Session, genre: str | None) -> list[BookWithLoan]:
    """Неудалённые книги (жанра), сначала свободные, потом по названию — как в боте."""
    stmt = (
        select(LibraryBook, LibraryLoan)
        .outerjoin(
            LibraryLoan,
            and_(LibraryLoan.book_id == LibraryBook.id, LibraryLoan.returned_at.is_(None)),
        )
        .where(LibraryBook.removed_at.is_(None))
        .order_by(LibraryLoan.id.is_not(None), LibraryBook.title, LibraryBook.id)
    )
    if genre is not None:
        stmt = stmt.where(LibraryBook.genre == genre)
    return [BookWithLoan(b, loan) for b, loan in session.execute(stmt).all()]


def lock_book(session: Session, book_id: int) -> BookWithLoan | None:
    """Книга под ``FOR UPDATE`` и её невозвращённая выдача. None — книги нет."""
    book = session.scalars(
        select(LibraryBook)
        .where(LibraryBook.id == book_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one_or_none()
    if book is None:
        return None
    loan = session.scalars(
        select(LibraryLoan)
        .where(LibraryLoan.book_id == book.id, LibraryLoan.returned_at.is_(None))
        .execution_options(populate_existing=True)
    ).one_or_none()
    return BookWithLoan(book, loan)


def lock_loan(session: Session, loan_id: int) -> LoanWithBook | None:
    """Выдача под ``FOR UPDATE`` вместе с книгой. None — выдачи нет."""
    row = session.execute(
        select(LibraryLoan, LibraryBook)
        .join(LibraryBook, LibraryBook.id == LibraryLoan.book_id)
        .where(LibraryLoan.id == loan_id)
        .with_for_update(of=LibraryLoan)
        .execution_options(populate_existing=True)
    ).one_or_none()
    return None if row is None else LoanWithBook(row[0], row[1])


def my_loans(session: Session, huid: uuid.UUID) -> list[LoanWithBook]:
    """Невозвращённые выдачи пользователя, по сроку (просроченные тоже — Д-3)."""
    rows = session.execute(
        select(LibraryLoan, LibraryBook)
        .join(LibraryBook, LibraryBook.id == LibraryLoan.book_id)
        .where(LibraryLoan.user_huid == huid, LibraryLoan.returned_at.is_(None))
        .order_by(LibraryLoan.due_on, LibraryLoan.id)
    ).all()
    return [LoanWithBook(loan, b) for loan, b in rows]


def open_loans(session: Session, *, overdue: bool, today: date) -> list[LoanWithBook]:
    """Невозвращённые выдачи: просроченные (``due_on < today``) или нет."""
    due_filter = LibraryLoan.due_on < today if overdue else LibraryLoan.due_on >= today
    rows = session.execute(
        select(LibraryLoan, LibraryBook)
        .join(LibraryBook, LibraryBook.id == LibraryLoan.book_id)
        .where(LibraryLoan.returned_at.is_(None), due_filter)
        .order_by(LibraryLoan.due_on, LibraryLoan.id)
    ).all()
    return [LoanWithBook(loan, b) for loan, b in rows]


def history(session: Session) -> list[LoanWithBook]:
    """Все выдачи для выгрузки; порядок как в боте: по дате возврата, затем по сроку."""
    rows = session.execute(
        select(LibraryLoan, LibraryBook)
        .join(LibraryBook, LibraryBook.id == LibraryLoan.book_id)
        .order_by(
            LibraryLoan.returned_at.desc().nulls_last(),
            LibraryLoan.due_on.desc(),
            LibraryLoan.id.desc(),
        )
    ).all()
    return [LoanWithBook(loan, b) for loan, b in rows]


# --- оценки и обсуждения ---


@dataclass(frozen=True)
class RatingSummary:
    avg: float | None
    count: int


NO_RATINGS = RatingSummary(avg=None, count=0)


def rating_summaries(session: Session, book_ids: Iterable[int]) -> dict[int, RatingSummary]:
    """Средний балл и число оценок по книгам одним запросом.

    Книг без оценок в ответе нет — вызывающий берёт ``NO_RATINGS``.
    """
    ids = sorted(set(book_ids))
    if not ids:
        return {}
    rows = session.execute(
        select(LibraryRating.book_id, func.avg(LibraryRating.score), func.count())
        .where(LibraryRating.book_id.in_(ids))
        .group_by(LibraryRating.book_id)
    ).all()
    return {book_id: RatingSummary(float(avg), int(count)) for book_id, avg, count in rows}


def my_score(session: Session, book_id: int, huid: uuid.UUID) -> int | None:
    return session.scalar(
        select(LibraryRating.score).where(
            LibraryRating.book_id == book_id, LibraryRating.user_huid == huid
        )
    )


def upsert_rating(
    session: Session,
    *,
    book_id: int,
    huid: uuid.UUID,
    name: str | None,
    channel: Channel,
    score: int,
    now: datetime,
) -> None:
    """Поставить или заменить оценку (``INSERT … ON CONFLICT DO UPDATE``, О-2)."""
    stmt = pg_insert(LibraryRating).values(
        book_id=book_id,
        user_huid=huid,
        user_name=name,
        channel=channel,
        score=score,
        created_at=now,
        updated_at=now,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[LibraryRating.book_id, LibraryRating.user_huid],
        set_={
            "score": stmt.excluded.score,
            "user_name": stmt.excluded.user_name,
            "updated_at": stmt.excluded.updated_at,
        },
    )
    session.execute(stmt)


def live_book(session: Session, book_id: int) -> LibraryBook | None:
    """Неудалённая книга без блокировки (оценки и обсуждение строку книги не меняют)."""
    return session.scalars(
        select(LibraryBook).where(LibraryBook.id == book_id, LibraryBook.removed_at.is_(None))
    ).one_or_none()


def book_comments(session: Session, book_id: int) -> list[LibraryComment]:
    """Неудалённые комментарии книги, новые сверху."""
    return list(
        session.scalars(
            select(LibraryComment)
            .where(LibraryComment.book_id == book_id, LibraryComment.removed_at.is_(None))
            .order_by(LibraryComment.created_at.desc(), LibraryComment.id.desc())
        )
    )


def lock_comment(session: Session, comment_id: int) -> LibraryComment | None:
    """Комментарий под ``FOR UPDATE`` (удаление). None — нет такого."""
    return session.scalars(
        select(LibraryComment)
        .where(LibraryComment.id == comment_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one_or_none()
