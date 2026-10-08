from __future__ import annotations

import uuid
from datetime import datetime

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from pa_booking.db import library as repo
from pa_booking.db.models import LibraryBook, LibraryComment, LibraryRating
from pa_booking.domain.identity import Channel
from pa_booking.domain.moscow import MSK

pytestmark = pytest.mark.db

USER = uuid.UUID("11111111-1111-1111-1111-111111111111")
OTHER = uuid.UUID("22222222-2222-2222-2222-222222222222")
NOW = datetime(2026, 10, 5, 10, 0, tzinfo=MSK)


def _book(s: Session, *, removed: bool = False) -> int:
    book = LibraryBook(
        genre="Роман",
        author="Булгаков",
        title="Мастер",
        description="—",
        removed_at=NOW if removed else None,
    )
    s.add(book)
    s.commit()
    return book.id


def _rate(s: Session, book_id: int, huid: uuid.UUID, score: int) -> None:
    repo.upsert_rating(
        s, book_id=book_id, huid=huid, name=None, channel=Channel.LK, score=score, now=NOW
    )
    s.commit()


def test_upsert_replaces_score_of_same_user(db_session: Session) -> None:
    book_id = _book(db_session)
    _rate(db_session, book_id, USER, 5)
    _rate(db_session, book_id, USER, 2)

    count = db_session.scalar(select(func.count()).select_from(LibraryRating))
    assert count == 1
    assert repo.my_score(db_session, book_id, USER) == 2
    assert repo.my_score(db_session, book_id, OTHER) is None


def test_rating_summaries_avg_and_count_only_for_rated(db_session: Session) -> None:
    rated = _book(db_session)
    unrated = _book(db_session)
    _rate(db_session, rated, USER, 5)
    _rate(db_session, rated, OTHER, 4)

    summaries = repo.rating_summaries(db_session, [rated, unrated])

    assert summaries == {rated: repo.RatingSummary(avg=4.5, count=2)}
    assert summaries.get(unrated, repo.NO_RATINGS) == repo.RatingSummary(None, 0)
    assert repo.rating_summaries(db_session, []) == {}


def test_live_book_hides_removed(db_session: Session) -> None:
    alive = _book(db_session)
    removed = _book(db_session, removed=True)
    assert repo.live_book(db_session, alive) is not None
    assert repo.live_book(db_session, removed) is None
    assert repo.live_book(db_session, 999_999) is None


def test_book_comments_newest_first_without_removed(db_session: Session) -> None:
    book_id = _book(db_session)
    rows = [
        LibraryComment(
            book_id=book_id,
            user_huid=USER,
            channel=Channel.LK,
            body=body,
            created_at=datetime(2026, 10, day, 10, 0, tzinfo=MSK),
            removed_at=NOW if removed else None,
        )
        for body, day, removed in [("старый", 1, False), ("новый", 3, False), ("удалён", 2, True)]
    ]
    db_session.add_all(rows)
    db_session.commit()

    assert [c.body for c in repo.book_comments(db_session, book_id)] == ["новый", "старый"]
    locked = repo.lock_comment(db_session, rows[2].id)
    assert locked is not None and locked.removed_at is not None
    assert repo.lock_comment(db_session, 999_999) is None
