"""Схема оценок и обсуждений: ограничения БД и откат миграции 0006."""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from pa_booking.db.models import LibraryBook, LibraryComment, LibraryRating
from pa_booking.domain.identity import Channel
from tests.integration.conftest import ROOT

pytestmark = pytest.mark.db

USER = uuid.UUID("11111111-1111-1111-1111-111111111111")
SOCIAL = {"library_ratings", "library_comments"}


def _book(s: Session) -> LibraryBook:
    book = LibraryBook(genre="Роман", author="Булгаков", title="Мастер", description="—")
    s.add(book)
    s.flush()
    return book


def _rating(book: LibraryBook, score: int) -> LibraryRating:
    return LibraryRating(book_id=book.id, user_huid=USER, channel=Channel.LK, score=score)


@pytest.mark.parametrize("score", [0, 6])
def test_score_outside_1_5_rejected_by_db(db_session: Session, score: int) -> None:
    book = _book(db_session)
    db_session.add(_rating(book, score))
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_second_rating_of_same_user_rejected_by_pk(db_session: Session) -> None:
    book = _book(db_session)
    db_session.add(_rating(book, 5))
    db_session.flush()
    db_session.expunge_all()
    db_session.add(_rating(book, 3))
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_comment_defaults(db_session: Session) -> None:
    book = _book(db_session)
    comment = LibraryComment(book_id=book.id, user_huid=USER, channel=Channel.LK, body="Хорошо")
    db_session.add(comment)
    db_session.flush()
    db_session.refresh(comment)
    assert comment.removed_at is None
    assert comment.removed_by_librarian is False
    assert comment.created_at is not None


@pytest.fixture
def fresh_url(pg_engine: Engine) -> Iterator[str]:
    """Своя база в том же контейнере: общая ``pg_engine`` должна остаться на head."""
    name = f"m0006_{uuid.uuid4().hex[:8]}"
    admin = pg_engine.execution_options(isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f"CREATE DATABASE {name}"))
    url = pg_engine.url.set(database=name).render_as_string(hide_password=False)
    try:
        yield url
    finally:
        with admin.connect() as conn:
            conn.execute(text(f"DROP DATABASE {name} WITH (FORCE)"))


def test_migration_0006_downgrades_and_upgrades(fresh_url: str) -> None:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", fresh_url.replace("%", "%%"))
    engine = create_engine(fresh_url)
    try:
        command.upgrade(cfg, "head")
        assert set(inspect(engine).get_table_names()) >= SOCIAL

        command.downgrade(cfg, "0005_user_huid")
        assert not SOCIAL & set(inspect(engine).get_table_names())
        # Тип канала общий с выдачами — откат 0006 его не трогает.
        assert "library_loans" in inspect(engine).get_table_names()

        command.upgrade(cfg, "head")
        assert set(inspect(engine).get_table_names()) >= SOCIAL
    finally:
        engine.dispose()
