# Библиотека: оценки и обсуждения — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Оценка книг 1–5 со средним баллом в каталоге и обсуждение (комментарии) в карточке книги ЛК.

**Architecture:** Две новые таблицы в `pa_booking_service` (`library_ratings` с upsert по составному PK, `library_comments` с мягким удалением), средний балл — агрегатом на лету через общий хелпер `rating_summaries`, который зовут все ручки с `BookOut`. BFF — тонкий passthrough новых ручек. SPA — подвал плитки с рейтингом, компоненты `BookRating` и `BookComments` в карточке книги.

**Tech Stack:** FastAPI + Pydantic v2, sync SQLAlchemy 2.0 + Alembic (PostgreSQL 16), pytest + testcontainers; BFF — FastAPI passthrough; SPA — React + TypeScript + Vite, vitest + jsdom.

**Spec:** `pa_booking_service/docs/superpowers/specs/2026-10-08-library-ratings-comments-design.md`

## Global Constraints

- Два репозитория: `/mnt/ssd/projects/pa_booking_service` (задачи 1–5, 9) и `/mnt/ssd/projects/pa_bff` (задачи 6–8). У каждой задачи указан репозиторий.
- **Git — только пользователь.** Исполнитель не запускает `git add/commit/push`; в конце задачи — предложенное сообщение коммита (англ., `prefix(scope): summary`, ≤72 символов).
- **Кто запускает проверки:** сервис — `ruff`, `mypy --strict` и быстрые unit-тесты (`pytest tests/unit/<file> -q`) — исполнитель сам; интеграционные (`-m db`, testcontainers) и полный прогон — пользователь по точной команде. BFF — `ruff`/`mypy --strict` сам, `pytest` — пользователь. SPA — `eslint`/`tsc --noEmit` сам, `vitest` и `npm run build` — пользователь.
- Код, докстринги, тексты UI — по-русски.
- Оценка — целое 1–5 (О-1, О-2); одна на (книга, HUID), повторная заменяет.
- Комментарий — текст после `strip` длиной 1–2000 (О-5), без редактирования.
- Чужой/удалённый комментарий и удалённая/несуществующая книга → 404 `not_found` (существование чужого не раскрываем).
- Запись (оценить, написать, удалить своё) — зависимость `Me` (нужен HUID; ЛК без HUID → 409 `express_not_linked`); чтение — `User` (HUID может быть `None` → `mine = null/false`); админ-удаление — `Librarian` (только канал бота).
- Колонка БД комментария — `body`; поле API — `text`.
- SPA: текст комментария выводится только как текст (никакого `dangerouslySetInnerHTML`).
- Средний балл в UI — одна цифра после запятой, разделитель — запятая (`ru-RU`).

## Review Focus

1. **`BookOut` вне каталога** (Мои книги, бронь, продление, возврат, админ-выдачи) — должен нести настоящие `rating_avg/rating_count`, а не `null/0`. Тест: `test_loans_carry_book_rating` (задача 4).
2. **Пользователь ЛК без привязанного eXpress** открывает карточку — чтение работает (`mine = null`, `mine = false`), запись даёт 409 `express_not_linked`. Тест: `test_lk_without_huid_reads_but_cannot_write` (задача 5).
3. **Комментарий из одних пробелов / ровно 2000 / 2001 символ** — 422 / 201 / 422. Тесты: `test_comment_text_bounds` (задача 2) и `test_blank_comment_is_422` (задача 5).
4. **Esc в подтверждении удаления комментария** не должен закрывать заодно карточку книги. Реализовано проверкой числа открытых диалогов в `BookDialog`; тест: «Esc в подтверждении не закрывает карточку» (задача 8).
5. **Повторное удаление уже удалённого комментария** (двойной клик, вторая вкладка) — 404, а не 204 и не 500. Тест: `test_delete_own_comment_hides_it_and_second_delete_is_404` (задача 5).

---

### Task 1: Модели и миграция `0006_library_social` (pa_booking_service)

**Files:**
- Modify: `src/pa_booking/db/models.py` (импорт `SmallInteger`; классы после `LibraryLoan`)
- Create: `alembic/versions/0006_library_social.py`
- Create: `tests/integration/test_library_social_schema.py`

**Interfaces:**
- Produces: ORM `LibraryRating(book_id, user_huid, user_name, channel, score, created_at, updated_at)`, `LibraryComment(id, book_id, user_huid, user_name, channel, body, created_at, removed_at, removed_by_librarian)`.

- [ ] **Step 1: Написать падающие тесты схемы**

`tests/integration/test_library_social_schema.py`:

```python
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
        assert SOCIAL <= set(inspect(engine).get_table_names())

        command.downgrade(cfg, "0005_user_huid")
        assert not SOCIAL & set(inspect(engine).get_table_names())
        # Тип канала общий с выдачами — откат 0006 его не трогает.
        assert "library_loans" in inspect(engine).get_table_names()

        command.upgrade(cfg, "head")
        assert SOCIAL <= set(inspect(engine).get_table_names())
    finally:
        engine.dispose()
```

- [ ] **Step 2: Проверить, что тесты падают** (запускает пользователь)

Run: `pytest tests/integration/test_library_social_schema.py -m db -q`
Expected: FAIL — `ImportError: cannot import name 'LibraryComment'`.

- [ ] **Step 3: Модели**

В `src/pa_booking/db/models.py` добавить `SmallInteger` в импорт из `sqlalchemy` (по алфавиту, после `Index`):

```python
from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    SmallInteger,
    String,
    Text,
    func,
    text,
)
```

В конец файла (после `LibraryLoan`):

```python
class LibraryRating(Base):
    """Оценка книги: одна на (книга, HUID), повторная — upsert (спека О-2)."""

    __tablename__ = "library_ratings"

    book_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("library_books.id"), primary_key=True
    )
    user_huid: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    user_name: Mapped[str | None] = mapped_column(String(256))
    channel: Mapped[Channel] = mapped_column(_pg_enum(Channel, "booking_channel"), nullable=False)
    score: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (CheckConstraint("score BETWEEN 1 AND 5", name="ck_library_ratings_score"),)


class LibraryComment(Base):
    """Комментарий в обсуждении книги. Удаление мягкое (автор или библиотекарь).

    Колонка — ``body``: атрибут ``text`` перекрыл бы ``sqlalchemy.text`` в теле класса.
    """

    __tablename__ = "library_comments"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    book_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("library_books.id"), nullable=False)
    user_huid: Mapped[uuid.UUID] = mapped_column(nullable=False)
    user_name: Mapped[str | None] = mapped_column(String(256))
    channel: Mapped[Channel] = mapped_column(_pg_enum(Channel, "booking_channel"), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    removed_by_librarian: Mapped[bool] = mapped_column(
        nullable=False, server_default=text("false")
    )

    __table_args__ = (
        Index(
            "ix_library_comments_book",
            "book_id",
            "created_at",
            postgresql_where=text("removed_at IS NULL"),
        ),
    )
```

- [ ] **Step 4: Миграция**

`alembic/versions/0006_library_social.py`:

```python
"""Библиотека: оценки и обсуждения книг.

Тип ``booking_channel`` создан в 0005 — здесь переиспользуется (``create_type=False``)
и при откате не удаляется.

Revision ID: 0006_library_social
Revises: 0005_user_huid
Create Date: 2026-10-08
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006_library_social"
down_revision: str | None = "0005_user_huid"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

booking_channel = postgresql.ENUM("lk", "express", name="booking_channel", create_type=False)


def upgrade() -> None:
    op.create_table(
        "library_ratings",
        sa.Column(
            "book_id", sa.BigInteger(), sa.ForeignKey("library_books.id"), primary_key=True
        ),
        sa.Column("user_huid", sa.Uuid(), primary_key=True),
        sa.Column("user_name", sa.String(256), nullable=True),
        sa.Column("channel", booking_channel, nullable=False),
        sa.Column("score", sa.SmallInteger(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("score BETWEEN 1 AND 5", name="ck_library_ratings_score"),
    )
    op.create_table(
        "library_comments",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("book_id", sa.BigInteger(), sa.ForeignKey("library_books.id"), nullable=False),
        sa.Column("user_huid", sa.Uuid(), nullable=False),
        sa.Column("user_name", sa.String(256), nullable=True),
        sa.Column("channel", booking_channel, nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("removed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "removed_by_librarian",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.create_index(
        "ix_library_comments_book",
        "library_comments",
        ["book_id", "created_at"],
        postgresql_where=sa.text("removed_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_table("library_comments")
    op.drop_table("library_ratings")
```

- [ ] **Step 5: ruff + mypy** (исполнитель)

Run: `ruff check src alembic tests && ruff format --check src alembic tests && mypy --strict src`
Expected: без ошибок.

- [ ] **Step 6: Прогнать тесты схемы и существующие тесты миграций** (пользователь)

Run: `pytest tests/integration/test_library_social_schema.py tests/integration/test_migrations.py -m db -q`
Expected: PASS (включая `test_migration_columns_match_models` — модели совпадают с миграцией).

- [ ] **Step 7: Предложить коммит**

`feat(library): add ratings and comments tables`

---

### Task 2: Схемы API (pa_booking_service)

**Files:**
- Modify: `src/pa_booking/api/library_schemas.py`
- Create: `tests/unit/test_library_schemas.py`

**Interfaces:**
- Produces: `RatingIn(score: int)`, `RatingOut(avg: float | None, count: int, mine: int | None)`, `CommentIn(text: str)`, `CommentOut(id: int, author_name: str, text: str, created_at: datetime, mine: bool)`; в `BookOut` новые обязательные поля `rating_avg: float | None`, `rating_count: int`; константа `COMMENT_MAX = 2000`.

- [ ] **Step 1: Падающие тесты**

`tests/unit/test_library_schemas.py`:

```python
from __future__ import annotations

import pytest
from pydantic import ValidationError

from pa_booking.api.library_schemas import COMMENT_MAX, CommentIn, RatingIn


@pytest.mark.parametrize("score", [0, 6, -1])
def test_score_outside_1_5_rejected(score: int) -> None:
    with pytest.raises(ValidationError):
        RatingIn(score=score)


@pytest.mark.parametrize("score", [1, 5])
def test_score_bounds_accepted(score: int) -> None:
    assert RatingIn(score=score).score == score


def test_comment_text_bounds() -> None:
    assert CommentIn(text="  Хорошо  ").text == "Хорошо"
    assert len(CommentIn(text="я" * COMMENT_MAX).text) == COMMENT_MAX
    for bad in ["", "   \n ", "я" * (COMMENT_MAX + 1)]:
        with pytest.raises(ValidationError):
            CommentIn(text=bad)
```

- [ ] **Step 2: Проверить падение** (исполнитель)

Run: `pytest tests/unit/test_library_schemas.py -q`
Expected: FAIL — `ImportError: cannot import name 'COMMENT_MAX'`.

- [ ] **Step 3: Реализация**

В `src/pa_booking/api/library_schemas.py` заменить импорт pydantic и добавить после `Description`:

```python
from pydantic import BaseModel, Field, StringConstraints
```

```python
COMMENT_MAX = 2000
Score = Annotated[int, Field(ge=1, le=5)]
CommentText = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=COMMENT_MAX)
]
```

`BookOut` — добавить поля в конец:

```python
class BookOut(BaseModel):
    id: int
    genre: str
    author: str
    title: str
    description: str
    available: bool
    rating_avg: float | None
    rating_count: int
```

В конец файла:

```python
class RatingIn(BaseModel):
    score: Score


class RatingOut(BaseModel):
    """Сводка оценок книги; ``mine`` — оценка текущего пользователя (нет HUID — null)."""

    avg: float | None
    count: int
    mine: int | None


class CommentIn(BaseModel):
    text: CommentText


class CommentOut(BaseModel):
    id: int
    author_name: str
    text: str
    created_at: datetime
    mine: bool
```

- [ ] **Step 4: Тесты и линтеры** (исполнитель)

Run: `pytest tests/unit/test_library_schemas.py -q && ruff check src tests && mypy --strict src`
Expected: тесты PASS. `mypy` упадёт на `api/library.py` (`BookOut` без новых полей) — это чинит задача 4; если исполняется по порядку, допустимо, отметить в отчёте. Чтобы не оставлять красный mypy между задачами, задачи 2–4 можно сдавать одним батчем.

- [ ] **Step 5: Предложить коммит** — общий с задачей 4 (см. там).

---

### Task 3: Репозиторий оценок и обсуждений (pa_booking_service)

**Files:**
- Modify: `src/pa_booking/db/library.py`
- Create: `tests/integration/test_library_social_repo.py`

**Interfaces:**
- Consumes: `LibraryRating`, `LibraryComment` (задача 1).
- Produces:
  - `RatingSummary(avg: float | None, count: int)` (frozen dataclass), `NO_RATINGS = RatingSummary(None, 0)`
  - `rating_summaries(session: Session, book_ids: Iterable[int]) -> dict[int, RatingSummary]` — только книги с оценками
  - `my_score(session: Session, book_id: int, huid: uuid.UUID) -> int | None`
  - `upsert_rating(session: Session, *, book_id: int, huid: uuid.UUID, name: str | None, channel: Channel, score: int, now: datetime) -> None`
  - `live_book(session: Session, book_id: int) -> LibraryBook | None`
  - `book_comments(session: Session, book_id: int) -> list[LibraryComment]` — неудалённые, новые сверху
  - `lock_comment(session: Session, comment_id: int) -> LibraryComment | None`

- [ ] **Step 1: Падающие тесты**

`tests/integration/test_library_social_repo.py`:

```python
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
```

- [ ] **Step 2: Проверить падение** (пользователь)

Run: `pytest tests/integration/test_library_social_repo.py -m db -q`
Expected: FAIL — `AttributeError: module 'pa_booking.db.library' has no attribute 'upsert_rating'`.

- [ ] **Step 3: Реализация**

В `src/pa_booking/db/library.py`: обновить докстринг модуля (первая строка — `"""Персистентность библиотеки: каталог, выдачи, оценки и обсуждения.`), импорты:

```python
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import and_, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from pa_booking.db.models import LibraryBook, LibraryComment, LibraryLoan, LibraryRating
from pa_booking.domain.identity import Channel
```

В конец файла:

```python
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
```

- [ ] **Step 4: ruff + mypy** (исполнитель)

Run: `ruff check src tests && ruff format --check src tests && mypy --strict src`
Expected: чисто (кроме ожидаемой ошибки `BookOut` в `api/library.py`, если задача 4 ещё не сделана).

- [ ] **Step 5: Тесты** (пользователь)

Run: `pytest tests/integration/test_library_social_repo.py -m db -q`
Expected: PASS.

- [ ] **Step 6: Предложить коммит** — общий с задачей 4.

---

### Task 4: Рейтинг в `BookOut` и ручки оценки (pa_booking_service)

**Files:**
- Modify: `src/pa_booking/api/library.py`
- Create: `tests/integration/test_api_library_social.py`

**Interfaces:**
- Consumes: `repo.rating_summaries`, `repo.NO_RATINGS`, `repo.RatingSummary`, `repo.my_score`, `repo.upsert_rating`, `repo.live_book` (задача 3); `RatingIn`, `RatingOut` (задача 2).
- Produces: `GET/PUT /library/books/{book_id}/rating`; хелперы модуля `_ratings(session, books) -> dict[int, RatingSummary]`, `_require_live_book(session, book_id) -> LibraryBook` (нужен задаче 5).

- [ ] **Step 1: Падающие тесты**

`tests/integration/test_api_library_social.py` (задача 5 допишет сюда же):

```python
"""Оценки и обсуждения книг через API (спека 2026-10-08)."""

from __future__ import annotations

import uuid
from datetime import datetime

import httpx
import pytest
from sqlalchemy.orm import Session

from pa_booking.db.models import LibraryBook, LibraryComment
from pa_booking.domain.moscow import MSK
from tests.integration.conftest import LIB_ADMIN, ApiEnv, add_roster, bot_headers, lk_headers

pytestmark = pytest.mark.db

USER = uuid.UUID("11111111-1111-1111-1111-111111111111")
OTHER = uuid.UUID("22222222-2222-2222-2222-222222222222")


def add_book(db: Session, *, removed: bool = False) -> int:
    book = LibraryBook(
        genre="Роман",
        author="Булгаков",
        title="Мастер и Маргарита",
        description="—",
        removed_at=datetime(2026, 10, 1, tzinfo=MSK) if removed else None,
    )
    db.add(book)
    db.commit()
    return book.id


def headers(huid: uuid.UUID, name: str | None = None) -> dict[str, str]:
    return bot_headers(huid, module="library", name=name)


def rate(api: ApiEnv, book_id: int, score: int, user: uuid.UUID = USER) -> httpx.Response:
    return api.client.put(
        f"/library/books/{book_id}/rating", json={"score": score}, headers=headers(user)
    )


# --- оценки ---


def test_rating_of_unrated_book(api: ApiEnv, db_session: Session) -> None:
    book_id = add_book(db_session)
    r = api.client.get(f"/library/books/{book_id}/rating", headers=headers(USER))
    assert r.status_code == 200
    assert r.json() == {"avg": None, "count": 0, "mine": None}


def test_rerating_replaces_and_mine_is_per_user(api: ApiEnv, db_session: Session) -> None:
    book_id = add_book(db_session)
    assert rate(api, book_id, 5).json() == {"avg": 5.0, "count": 1, "mine": 5}
    assert rate(api, book_id, 2).json() == {"avg": 2.0, "count": 1, "mine": 2}
    assert rate(api, book_id, 5, user=OTHER).json() == {"avg": 3.5, "count": 2, "mine": 5}

    r = api.client.get(f"/library/books/{book_id}/rating", headers=headers(USER))
    assert r.json() == {"avg": 3.5, "count": 2, "mine": 2}


@pytest.mark.parametrize("score", [0, 6])
def test_score_out_of_range_is_422(api: ApiEnv, db_session: Session, score: int) -> None:
    assert rate(api, add_book(db_session), score).status_code == 422


def test_catalog_carries_rating(api: ApiEnv, db_session: Session) -> None:
    rated = add_book(db_session)
    unrated = add_book(db_session)
    rate(api, rated, 5)
    rate(api, rated, 4, user=OTHER)

    books = api.client.get("/library/books", headers=headers(USER)).json()

    by_id = {b["id"]: (b["rating_avg"], b["rating_count"]) for b in books}
    assert by_id == {rated: (4.5, 2), unrated: (None, 0)}


def test_loans_carry_book_rating(api: ApiEnv, db_session: Session) -> None:
    """BookOut внутри выдач — с настоящим рейтингом, не null/0 (Review Focus 1)."""
    book_id = add_book(db_session)
    rate(api, book_id, 4, user=OTHER)

    loan = api.client.post(f"/library/books/{book_id}/loan", headers=headers(USER))
    assert loan.status_code == 201
    assert (loan.json()["book"]["rating_avg"], loan.json()["book"]["rating_count"]) == (4.0, 1)

    mine = api.client.get("/library/loans/my", headers=headers(USER)).json()
    assert mine[0]["book"]["rating_count"] == 1

    admin = api.client.get(
        "/library/admin/loans?state=active", headers=headers(LIB_ADMIN)
    ).json()
    assert admin[0]["book"]["rating_avg"] == 4.0


def test_rating_of_removed_book_is_404(api: ApiEnv, db_session: Session) -> None:
    book_id = add_book(db_session, removed=True)
    assert api.client.get(f"/library/books/{book_id}/rating", headers=headers(USER)).status_code == 404
    assert rate(api, book_id, 5).status_code == 404
    assert rate(api, 999_999, 5).status_code == 404


def test_lk_user_rates_via_roster_huid(api: ApiEnv, db_session: Session) -> None:
    employee_id = add_roster(db_session, USER, "Иванов Иван")
    book_id = add_book(db_session)
    r = api.client.put(
        f"/library/books/{book_id}/rating", json={"score": 3}, headers=lk_headers(employee_id)
    )
    assert r.status_code == 200
    assert r.json()["mine"] == 3
```

- [ ] **Step 2: Проверить падение** (пользователь)

Run: `pytest tests/integration/test_api_library_social.py -m db -q`
Expected: FAIL — 404/405 на `/rating` и `KeyError: 'rating_avg'`.

- [ ] **Step 3: Реализация в `src/pa_booking/api/library.py`**

Импорты — добавить:

```python
from collections.abc import Iterable, Mapping
```

```python
from pa_booking.api.library_schemas import (
    AdminLoanOut,
    BookCreate,
    BookOut,
    BookUpdate,
    LoanOut,
    RatingIn,
    RatingOut,
)
```

```python
from pa_booking.db.library import NO_RATINGS, RatingSummary
```

Заменить `_book_out`, `_loan_out`, `_admin_loan_out` и добавить хелперы:

```python
Ratings = Mapping[int, RatingSummary]


def _ratings(session: Session, books: Iterable[LibraryBook]) -> dict[int, RatingSummary]:
    """Сводки оценок для всех книг ответа — одним запросом."""
    return repo.rating_summaries(session, (b.id for b in books))


def _book_out(book: LibraryBook, *, available: bool, ratings: Ratings) -> BookOut:
    rating = ratings.get(book.id, NO_RATINGS)
    return BookOut(
        id=book.id,
        genre=book.genre,
        author=book.author,
        title=book.title,
        description=book.description,
        available=available,
        rating_avg=rating.avg,
        rating_count=rating.count,
    )


def _loan_out(
    loan: LibraryLoan, book: LibraryBook, *, now: datetime, ratings: Ratings
) -> LoanOut:
    open_ = loan.returned_at is None
    return LoanOut(
        id=loan.id,
        book=_book_out(book, available=not open_, ratings=ratings),
        starts_on=loan.starts_on,
        due_on=loan.due_on,
        overdue=open_ and is_overdue(loan.due_on, now=now),
        returned_at=loan.returned_at,
    )


def _admin_loan_out(
    loan: LibraryLoan, book: LibraryBook, full_name: str, *, now: datetime, ratings: Ratings
) -> AdminLoanOut:
    return AdminLoanOut(
        **_loan_out(loan, book, now=now, ratings=ratings).model_dump(),
        user_huid=loan.user_huid,
        full_name=full_name,
        returned_by_librarian=loan.returned_by_librarian,
    )


def _require_live_book(session: Session, book_id: int) -> LibraryBook:
    """Неудалённая книга для оценок и обсуждения; иначе 404."""
    book = repo.live_book(session, book_id)
    if book is None:
        raise ApiError(404, "not_found", "Книга не найдена")
    return book
```

Обновить все вызовы (каждый — с `ratings=`):

```python
@router.get("/books", response_model=list[BookOut])
def list_books(session: DbSession, _user: User, genre: str | None = None) -> list[BookOut]:
    """Каталог: сначала свободные, потом по названию; со сводкой оценок."""
    rows = repo.catalog(session, genre)
    ratings = _ratings(session, (r.book for r in rows))
    return [_book_out(r.book, available=not r.on_loan, ratings=ratings) for r in rows]
```

В `loan_book` последняя строка:

```python
    return _loan_out(loan, locked.book, now=now, ratings=_ratings(session, [locked.book]))
```

`my_loans`:

```python
    rows = repo.my_loans(session, user.huid)
    ratings = _ratings(session, (r.book for r in rows))
    return [_loan_out(r.loan, r.book, now=now, ratings=ratings) for r in rows]
```

`extend_loan` и `return_loan` — последняя строка:

```python
    return _loan_out(locked.loan, locked.book, now=now, ratings=_ratings(session, [locked.book]))
```

`admin_add_book` — новая книга без оценок:

```python
    return _book_out(book, available=True, ratings={})
```

`admin_update_book` — последняя строка:

```python
    return _book_out(
        locked.book, available=not locked.on_loan, ratings=_ratings(session, [locked.book])
    )
```

`admin_loans`:

```python
    names = _names(session, rows)
    ratings = _ratings(session, (r.book for r in rows))
    return [
        _admin_loan_out(r.loan, r.book, names[r.loan.user_huid], now=now, ratings=ratings)
        for r in rows
    ]
```

`admin_return_loan` — последняя строка:

```python
    return _admin_loan_out(
        locked.loan, locked.book, name, now=now, ratings=_ratings(session, [locked.book])
    )
```

Новые ручки — после `return_loan`, перед секцией «библиотекарь»:

```python
# --- оценки ---


def _rating_out(session: Session, book_id: int, huid: uuid.UUID | None) -> RatingOut:
    summary = repo.rating_summaries(session, [book_id]).get(book_id, NO_RATINGS)
    mine = None if huid is None else repo.my_score(session, book_id, huid)
    return RatingOut(avg=summary.avg, count=summary.count, mine=mine)


@router.get("/books/{book_id}/rating", response_model=RatingOut)
def book_rating(book_id: int, session: DbSession, user: User) -> RatingOut:
    """Сводка оценок; ``mine`` — null, если HUID неизвестен (ЛК без привязки)."""
    _require_live_book(session, book_id)
    return _rating_out(session, book_id, user.huid)


@router.put("/books/{book_id}/rating", response_model=RatingOut)
def rate_book(
    book_id: int, body: RatingIn, session: DbSession, user: Me, now: Now
) -> RatingOut:
    """Поставить или изменить свою оценку (О-1, О-2)."""
    _require_live_book(session, book_id)
    repo.upsert_rating(
        session,
        book_id=book_id,
        huid=user.huid,
        name=user.name,
        channel=user.channel,
        score=body.score,
        now=now,
    )
    session.commit()
    return _rating_out(session, book_id, user.huid)
```

- [ ] **Step 4: ruff + mypy + unit** (исполнитель)

Run: `ruff check src tests && ruff format --check src tests && mypy --strict src && pytest tests/unit -q`
Expected: чисто, unit PASS.

- [ ] **Step 5: Интеграционные** (пользователь)

Run: `pytest tests/integration/test_api_library_social.py tests/integration/test_api_library.py -m db -q`
Expected: PASS (старые тесты библиотеки не сломаны новыми полями).

- [ ] **Step 6: Предложить коммит (задачи 2–4 одним батчем)**

```
feat(library): rate books and show average rating

- RatingIn/RatingOut schemas, rating fields in BookOut
- rating_summaries/upsert_rating repo helpers
- GET/PUT /library/books/{id}/rating; every BookOut carries real rating
```

---

### Task 5: Ручки обсуждения и админ-удаление (pa_booking_service)

**Files:**
- Modify: `src/pa_booking/api/library.py`
- Modify: `tests/integration/test_api_library_social.py` (дописать)
- Modify: `tests/integration/test_api_library.py:72-81` (список админ-ручек в `test_admin_endpoints_require_librarian_in_bot`)

**Interfaces:**
- Consumes: `_require_live_book` (задача 4), `repo.book_comments`, `repo.lock_comment` (задача 3), `CommentIn`, `CommentOut` (задача 2), `display_names` (уже импортирован).
- Produces: `GET/POST /library/books/{book_id}/comments`, `DELETE /library/comments/{comment_id}`, `DELETE /library/admin/comments/{comment_id}`.

- [ ] **Step 1: Падающие тесты** — дописать в `tests/integration/test_api_library_social.py`:

```python
# --- обсуждение ---


def post_comment(
    api: ApiEnv, book_id: int, text: str, user: uuid.UUID = USER, name: str | None = None
) -> httpx.Response:
    return api.client.post(
        f"/library/books/{book_id}/comments", json={"text": text}, headers=headers(user, name)
    )


def comments(api: ApiEnv, book_id: int, user: uuid.UUID = USER) -> list[dict[str, object]]:
    r = api.client.get(f"/library/books/{book_id}/comments", headers=headers(user))
    assert r.status_code == 200
    result: list[dict[str, object]] = r.json()
    return result


def test_comments_newest_first_with_author_and_mine(api: ApiEnv, db_session: Session) -> None:
    book_id = add_book(db_session)
    first = post_comment(api, book_id, "  Понравилось  ", name="Иванов Иван")
    assert first.status_code == 201
    assert first.json()["text"] == "Понравилось"
    api.clock.now = datetime(2026, 10, 6, 12, 0, tzinfo=MSK)
    post_comment(api, book_id, "Скучно", user=OTHER, name="Петров Пётр")

    rows = comments(api, book_id)

    assert [(c["author_name"], c["text"], c["mine"]) for c in rows] == [
        ("Петров Пётр", "Скучно", False),
        ("Иванов Иван", "Понравилось", True),
    ]


def test_blank_comment_is_422(api: ApiEnv, db_session: Session) -> None:
    book_id = add_book(db_session)
    assert post_comment(api, book_id, "   ").status_code == 422
    assert post_comment(api, book_id, "я" * 2001).status_code == 422
    assert post_comment(api, book_id, "я" * 2000).status_code == 201


def test_comments_of_removed_book_are_404(api: ApiEnv, db_session: Session) -> None:
    book_id = add_book(db_session, removed=True)
    r = api.client.get(f"/library/books/{book_id}/comments", headers=headers(USER))
    assert r.status_code == 404
    assert post_comment(api, book_id, "Текст").status_code == 404


def test_delete_own_comment_hides_it_and_second_delete_is_404(
    api: ApiEnv, db_session: Session
) -> None:
    book_id = add_book(db_session)
    comment_id = post_comment(api, book_id, "Удалю").json()["id"]

    first = api.client.delete(f"/library/comments/{comment_id}", headers=headers(USER))
    second = api.client.delete(f"/library/comments/{comment_id}", headers=headers(USER))

    assert (first.status_code, second.status_code) == (204, 404)
    assert comments(api, book_id) == []


def test_cannot_delete_others_comment(api: ApiEnv, db_session: Session) -> None:
    book_id = add_book(db_session)
    comment_id = post_comment(api, book_id, "Моё").json()["id"]

    r = api.client.delete(f"/library/comments/{comment_id}", headers=headers(OTHER))

    assert r.status_code == 404
    assert len(comments(api, book_id)) == 1


def test_librarian_deletes_any_comment(api: ApiEnv, db_session: Session) -> None:
    book_id = add_book(db_session)
    comment_id = post_comment(api, book_id, "Спам").json()["id"]

    r = api.client.delete(f"/library/admin/comments/{comment_id}", headers=headers(LIB_ADMIN))

    assert r.status_code == 204
    assert comments(api, book_id) == []
    stored = db_session.get(LibraryComment, comment_id)
    assert stored is not None and stored.removed_by_librarian is True
    again = api.client.delete(f"/library/admin/comments/{comment_id}", headers=headers(LIB_ADMIN))
    assert again.status_code == 404


def test_lk_without_huid_reads_but_cannot_write(api: ApiEnv, db_session: Session) -> None:
    """Review Focus 2: ЛК без привязки eXpress — читать можно, писать нельзя."""
    book_id = add_book(db_session)
    post_comment(api, book_id, "Есть", user=OTHER)
    h = lk_headers(add_roster(db_session, None, "Без Привязки"))

    rating = api.client.get(f"/library/books/{book_id}/rating", headers=h)
    listing = api.client.get(f"/library/books/{book_id}/comments", headers=h)
    put = api.client.put(f"/library/books/{book_id}/rating", json={"score": 5}, headers=h)
    post = api.client.post(f"/library/books/{book_id}/comments", json={"text": "Т"}, headers=h)

    assert rating.json()["mine"] is None
    assert [c["mine"] for c in listing.json()] == [False]
    assert (put.status_code, put.json()["code"]) == (409, "express_not_linked")
    assert (post.status_code, post.json()["code"]) == (409, "express_not_linked")
```

В `tests/integration/test_api_library.py` в параметризацию `test_admin_endpoints_require_librarian_in_bot` добавить строку:

```python
        ("DELETE", "/library/admin/comments/1"),
```

- [ ] **Step 2: Проверить падение** (пользователь)

Run: `pytest tests/integration/test_api_library_social.py tests/integration/test_api_library.py -m db -q`
Expected: FAIL — 404/405 на `/comments`.

- [ ] **Step 3: Реализация** — в `src/pa_booking/api/library.py`.

Импорты: в список из `library_schemas` добавить `CommentIn`, `CommentOut`; из `db.models` — `LibraryComment`:

```python
from pa_booking.db.models import LibraryBook, LibraryComment, LibraryLoan
```

После ручек оценок:

```python
# --- обсуждение ---


def _comment_out(
    comment: LibraryComment, names: Mapping[uuid.UUID, str], huid: uuid.UUID | None
) -> CommentOut:
    return CommentOut(
        id=comment.id,
        author_name=names[comment.user_huid],
        text=comment.body,
        created_at=comment.created_at,
        mine=comment.user_huid == huid,
    )


@router.get("/books/{book_id}/comments", response_model=list[CommentOut])
def list_comments(book_id: int, session: DbSession, user: User) -> list[CommentOut]:
    """Обсуждение книги, новые сверху; ФИО — как в выгрузках (ростер → снимок → HUID)."""
    _require_live_book(session, book_id)
    rows = repo.book_comments(session, book_id)
    names = display_names(session, [(c.user_huid, c.user_name) for c in rows])
    return [_comment_out(c, names, user.huid) for c in rows]


@router.post(
    "/books/{book_id}/comments", status_code=status.HTTP_201_CREATED, response_model=CommentOut
)
def add_comment(
    book_id: int, body: CommentIn, session: DbSession, user: Me, now: Now
) -> CommentOut:
    _require_live_book(session, book_id)
    comment = LibraryComment(
        book_id=book_id,
        user_huid=user.huid,
        user_name=user.name,
        channel=user.channel,
        body=body.text,
        created_at=now,
    )
    session.add(comment)
    session.commit()
    names = display_names(session, [(comment.user_huid, comment.user_name)])
    return _comment_out(comment, names, user.huid)


@router.delete("/comments/{comment_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_comment(comment_id: int, session: DbSession, user: Me, now: Now) -> Response:
    """Удалить свой комментарий (мягко). Чужой или уже удалённый — 404."""
    comment = repo.lock_comment(session, comment_id)
    if comment is None or comment.removed_at is not None or comment.user_huid != user.huid:
        raise ApiError(404, "not_found", "Комментарий не найден")
    comment.removed_at = now
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
```

В секцию библиотекаря, после `admin_remove_book`:

```python
@router.delete("/admin/comments/{comment_id}", status_code=status.HTTP_204_NO_CONTENT)
def admin_delete_comment(
    comment_id: int, session: DbSession, _admin: Librarian, now: Now
) -> Response:
    """Модерация: любой неудалённый комментарий (О-4)."""
    comment = repo.lock_comment(session, comment_id)
    if comment is None or comment.removed_at is not None:
        raise ApiError(404, "not_found", "Комментарий не найден")
    comment.removed_at = now
    comment.removed_by_librarian = True
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
```

- [ ] **Step 4: ruff + mypy + unit** (исполнитель)

Run: `ruff check src tests && ruff format --check src tests && mypy --strict src && pytest tests/unit -q`
Expected: чисто.

- [ ] **Step 5: Полный прогон сервиса** (пользователь)

Run: `pytest -m "not external" -q`
Expected: PASS.

- [ ] **Step 6: Предложить коммит**

`feat(library): add book discussions with author and librarian removal`

---

### Task 6: BFF — проксирование новых ручек (pa_bff)

**Files:**
- Modify: `src/pa_bff/api/library.py`
- Modify: `tests/api/test_booking.py` (параметризации `test_routes_forward_path_params_and_body`, `test_admin_routes_are_not_proxied`, новый тест 204)

**Interfaces:**
- Consumes: ручки сервиса задач 4–5; `proxy`, `JsonBody` (уже есть в модуле).
- Produces: `GET/PUT /api/library/books/{id}/rating`, `GET/POST /api/library/books/{id}/comments`, `DELETE /api/library/comments/{id}` — контракт для задач 7–8.

- [ ] **Step 1: Падающие тесты** — в `tests/api/test_booking.py`:

В параметризацию `test_routes_forward_path_params_and_body`, после строки `.../loans/9/return` (внутри списка, перед `],`):

```python
        ("GET", "/api/library/books/5/rating", None, None,
         ("GET", "/library/books/5/rating", [], None)),
        ("PUT", "/api/library/books/5/rating", None, {"score": 4},
         ("PUT", "/library/books/5/rating", [], {"score": 4})),
        ("GET", "/api/library/books/5/comments", None, None,
         ("GET", "/library/books/5/comments", [], None)),
        ("POST", "/api/library/books/5/comments", None, {"text": "Хорошо"},
         ("POST", "/library/books/5/comments", [], {"text": "Хорошо"})),
        ("DELETE", "/api/library/comments/3", None, None,
         ("DELETE", "/library/comments/3", [], None)),
```

В параметризацию `test_admin_routes_are_not_proxied`:

```python
        ("DELETE", "/api/library/admin/comments/3"),
```

Новый тест после `test_created_status_and_body_pass_through`:

```python
def test_no_content_passes_through_without_body() -> None:
    mesh = FakeMesh(status=204, body=b"")
    resp = _client(mesh).delete("/api/library/comments/3", headers=_headers())
    assert resp.status_code == 204
    assert resp.content == b""
```

Тип `body` в сигнатуре `test_routes_forward_path_params_and_body` — `dict[str, str] | None`; `{"score": 4}` с ним не сходится для mypy. Поменять аннотацию на `dict[str, Any] | None`.

- [ ] **Step 2: Проверить падение** (пользователь)

Run: `pytest tests/api/test_booking.py -q`
Expected: FAIL — 405 на новых путях.

- [ ] **Step 3: Реализация** — в конец `src/pa_bff/api/library.py`:

```python
@router.get("/books/{book_id}/rating")
def book_rating(book_id: int, user: User, mesh: Mesh) -> Response:
    """``{avg, count, mine}`` — сводка оценок книги."""
    return proxy(mesh, "GET", f"/library/books/{book_id}/rating", user=user)


@router.put("/books/{book_id}/rating")
def rate_book(book_id: int, user: User, mesh: Mesh, body: JsonBody) -> Response:
    """Оценка ``{score}`` 1–5; повторная заменяет прежнюю."""
    return proxy(mesh, "PUT", f"/library/books/{book_id}/rating", user=user, json=body)


@router.get("/books/{book_id}/comments")
def book_comments(book_id: int, user: User, mesh: Mesh) -> Response:
    return proxy(mesh, "GET", f"/library/books/{book_id}/comments", user=user)


@router.post("/books/{book_id}/comments")
def add_comment(book_id: int, user: User, mesh: Mesh, body: JsonBody) -> Response:
    """Комментарий ``{text}`` → 201."""
    return proxy(mesh, "POST", f"/library/books/{book_id}/comments", user=user, json=body)


@router.delete("/comments/{comment_id}")
def delete_comment(comment_id: int, user: User, mesh: Mesh) -> Response:
    """Удалить свой комментарий → 204; чужой — 404 от сервиса."""
    return proxy(mesh, "DELETE", f"/library/comments/{comment_id}", user=user)
```

- [ ] **Step 4: ruff + mypy** (исполнитель)

Run: `cd /mnt/ssd/projects/pa_bff && ruff check src tests && ruff format --check src tests && mypy --strict src`
Expected: чисто.

- [ ] **Step 5: Тесты** (пользователь)

Run: `pytest tests/api/test_booking.py -q`
Expected: PASS.

- [ ] **Step 6: Предложить коммит** — общий с задачами 7–8 (`pa_bff`), см. задачу 8.

---

### Task 7: SPA — data-слой, форматтеры, подвал плитки (pa_bff/frontend)

**Files:**
- Modify: `frontend/src/lib/library.ts`
- Modify: `frontend/src/lib/bookingUi.ts`
- Modify: `frontend/src/pages/library/Catalog.tsx`
- Modify: `frontend/src/pages/booking/Booking.css`
- Modify: `frontend/src/test/bookingUi.test.ts`
- Modify: `frontend/src/test/libraryPages.test.tsx`

**Interfaces:**
- Consumes: BFF-ручки задачи 6.
- Produces (для задачи 8):
  - `lib/library.ts`: `Book` + `rating_avg: number | null; rating_count: number`; `type Rating = { avg: number | null; count: number; mine: number | null }`; `type BookComment = { id: number; author_name: string; text: string; created_at: string; mine: boolean }`; `getRating(bookId: number): Promise<Rating>`; `rateBook(bookId: number, score: number): Promise<Rating>`; `listComments(bookId: number): Promise<BookComment[]>`; `addComment(bookId: number, text: string): Promise<BookComment>`; `deleteComment(commentId: number): Promise<void>`; `COMMENT_MAX = 2000`.
  - `lib/bookingUi.ts`: `formatRating(avg: number): string`, `ratingsLabel(n: number): string`, `formatMskDate(iso: string): string`.
  - CSS-классы `booking-book__foot`, `booking-book__rating`, `booking-book__rating--none`.

- [ ] **Step 1: Падающие тесты форматтеров** — в `frontend/src/test/bookingUi.test.ts` добавить `formatMskDate, formatRating, ratingsLabel` в импорт из `../lib/bookingUi` и в конец файла:

```ts
describe("рейтинг и даты обсуждения", () => {
  it("средний балл — одна цифра, запятая", () => {
    expect(formatRating(4.333)).toBe("4,3");
    expect(formatRating(4)).toBe("4,0");
  });

  it("склонение «оценка»", () => {
    expect([1, 2, 5, 11, 12, 21, 22, 25, 111].map(ratingsLabel)).toEqual([
      "1 оценка",
      "2 оценки",
      "5 оценок",
      "11 оценок",
      "12 оценок",
      "21 оценка",
      "22 оценки",
      "25 оценок",
      "111 оценок",
    ]);
  });

  it("дата комментария — по Москве", () => {
    // 21:30 UTC 5-го — уже 00:30 МСК 6-го.
    expect(formatMskDate("2026-10-05T21:30:00+00:00")).toBe("06.10.2026");
  });
});
```

- [ ] **Step 2: Падающие тесты плитки** — в `frontend/src/test/libraryPages.test.tsx`:

(а) `stubApi`: самый длинный совпавший ключ (иначе `GET /api/library/books` перехватит `…/books/1/rating`) и пустое тело для 204 (конструктор `Response` не принимает тело при 204):

```tsx
function stubApi(routes: Record<string, Reply>) {
  const fetchMock = vi.fn().mockImplementation((url: string, init?: RequestInit) => {
    const key = `${init?.method ?? "GET"} ${url}`;
    const match = Object.keys(routes)
      .filter((k) => key.startsWith(k))
      .sort((a, b) => b.length - a.length)[0];
    const reply = match ? routes[match] : { status: 404, body: { detail: "нет", code: "x" } };
    const status = reply.status ?? 200;
    return Promise.resolve(
      new Response(status === 204 ? null : JSON.stringify(reply.body), { status }),
    );
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}
```

(б) фикстура `book` — с рейтингом:

```tsx
const book = (id: number, available: boolean, rating_avg: number | null = null, rating_count = 0) => ({
  id,
  genre: "Роман",
  author: "Булгаков",
  title: `Книга ${id}`,
  description: `Описание ${id}`,
  available,
  rating_avg,
  rating_count,
});
```

(в) новый тест в `describe("Catalog", …)`:

```tsx
  it("подвал плитки: средний балл или «Нет оценок»", async () => {
    stubApi({
      "GET /api/library/genres": { body: [] },
      "GET /api/library/books": { body: [book(1, true, 4.333, 7), book(2, false)] },
    });
    await mount(<Catalog />);

    expect(tile("Книга 1")?.textContent).toContain("★ 4,3 · 7");
    expect(tile("Книга 2")?.textContent).toContain("Нет оценок");
    expect(tile("Книга 2")?.textContent).toContain("На руках");
  });
```

- [ ] **Step 3: Проверить падение** (пользователь)

Run: `cd frontend && npx vitest run src/test/bookingUi.test.ts src/test/libraryPages.test.tsx`
Expected: FAIL — `formatRating is not a function` / нет «★ 4,3 · 7».

- [ ] **Step 4: Форматтеры** — в конец `frontend/src/lib/bookingUi.ts`:

```ts
/** Средний балл: `4.333` → `4,3`. */
export function formatRating(avg: number): string {
  return avg.toLocaleString("ru-RU", { minimumFractionDigits: 1, maximumFractionDigits: 1 });
}

/** `1 оценка`, `3 оценки`, `5 оценок`, `21 оценка`. */
export function ratingsLabel(n: number): string {
  const mod10 = n % 10;
  const mod100 = n % 100;
  let word = "оценок";
  if (mod10 === 1 && mod100 !== 11) word = "оценка";
  else if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) word = "оценки";
  return `${n} ${word}`;
}

/** Момент (ISO с зоной) → дата по Москве `dd.mm.yyyy`. */
export function formatMskDate(iso: string): string {
  return new Intl.DateTimeFormat("ru-RU", {
    timeZone: "Europe/Moscow",
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
  }).format(new Date(iso));
}
```

- [ ] **Step 5: Data-слой** — `frontend/src/lib/library.ts`.

Обновить докстринг модуля: «Только ручки читателя (каталог, выдачи, оценки, обсуждение)…». `Book` и новые типы:

```ts
export type Book = {
  id: number;
  genre: string;
  author: string;
  title: string;
  description: string;
  available: boolean;
  rating_avg: number | null;
  rating_count: number;
};

/** Сводка оценок книги; `mine` — своя оценка (null — не ставил или нет HUID). */
export type Rating = { avg: number | null; count: number; mine: number | null };

export type BookComment = {
  id: number;
  author_name: string;
  text: string;
  created_at: string;
  mine: boolean;
};

/** Лимит длины комментария — как в сервисе (CommentIn). */
export const COMMENT_MAX = 2000;
```

После `const base = …`:

```ts
const json = (): HeadersInit => ({ ...authHeaders(), "Content-Type": "application/json" });
```

В конец файла:

```ts
export async function getRating(bookId: number): Promise<Rating> {
  const resp = await fetch(`${base()}/books/${bookId}/rating`, { headers: authHeaders() });
  if (!resp.ok) throw await codedError(resp, "загрузить оценки");
  return (await resp.json()) as Rating;
}

export async function rateBook(bookId: number, score: number): Promise<Rating> {
  const resp = await fetch(`${base()}/books/${bookId}/rating`, {
    method: "PUT",
    headers: json(),
    body: JSON.stringify({ score }),
  });
  if (!resp.ok) throw await codedError(resp, "поставить оценку");
  return (await resp.json()) as Rating;
}

/** Обсуждение книги, новые сверху. */
export async function listComments(bookId: number): Promise<BookComment[]> {
  const resp = await fetch(`${base()}/books/${bookId}/comments`, { headers: authHeaders() });
  if (!resp.ok) throw await codedError(resp, "загрузить обсуждение");
  return (await resp.json()) as BookComment[];
}

export async function addComment(bookId: number, text: string): Promise<BookComment> {
  const resp = await fetch(`${base()}/books/${bookId}/comments`, {
    method: "POST",
    headers: json(),
    body: JSON.stringify({ text }),
  });
  if (!resp.ok) throw await codedError(resp, "отправить комментарий");
  return (await resp.json()) as BookComment;
}

export async function deleteComment(commentId: number): Promise<void> {
  const resp = await fetch(`${base()}/comments/${commentId}`, {
    method: "DELETE",
    headers: authHeaders(),
  });
  if (!resp.ok) throw await codedError(resp, "удалить комментарий");
}
```

- [ ] **Step 6: Подвал плитки** — `frontend/src/pages/library/Catalog.tsx`.

Импорт: `import { errorCode, formatDate, formatRating } from "../../lib/bookingUi";`

Внутри `<button className="booking-book …">` заменить блок `{!book.available && (<span … booking-book__badge>…)}` на подвал:

```tsx
            <span className="booking-book__foot">
              {book.rating_avg !== null ? (
                <span className="booking-book__rating">
                  ★ {formatRating(book.rating_avg)} · {book.rating_count}
                </span>
              ) : (
                <span className="booking-book__rating booking-book__rating--none">
                  Нет оценок
                </span>
              )}
              {!book.available && (
                <span className="booking-badge booking-badge--cancel">На руках</span>
              )}
            </span>
```

- [ ] **Step 7: CSS** — `frontend/src/pages/booking/Booking.css`:

В `.booking-book` поменять `height: 7.25rem;` на `height: 8.5rem;`.

Удалить правила `.booking-book--taken .booking-book__meta--genre { … }` (с комментарием над ним) и `.booking-book__badge { … }`. В `Catalog.tsx` у плитки убрать модификатор: `className="booking-book"` (бейдж теперь в подвале, отступ под него не нужен).

Добавить после `.booking-book__meta--genre`:

```css
/* Подвал плитки: средний балл слева, «На руках» справа. */
.booking-book__foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.5rem;
  margin-top: 0.35rem;
}

.booking-book__rating {
  color: #fde68a;
  font-size: 0.78rem;
  font-weight: 700;
  white-space: nowrap;
}

.booking-book__rating--none {
  color: rgba(255, 255, 255, 0.38);
  font-weight: 500;
}
```

- [ ] **Step 8: eslint + tsc** (исполнитель)

Run: `cd frontend && npx tsc --noEmit && npx eslint src`
Expected: чисто.

- [ ] **Step 9: Тесты** (пользователь)

Run: `cd frontend && npx vitest run src/test/bookingUi.test.ts src/test/libraryPages.test.tsx`
Expected: PASS.

- [ ] **Step 10: Предложить коммит** — общий, см. задачу 8.

---

### Task 8: SPA — оценка и обсуждение в карточке книги (pa_bff/frontend)

**Files:**
- Create: `frontend/src/pages/library/BookRating.tsx`
- Create: `frontend/src/pages/library/BookComments.tsx`
- Modify: `frontend/src/pages/library/BookDialog.tsx`
- Modify: `frontend/src/pages/library/Catalog.tsx` (проп `onRated`)
- Modify: `frontend/src/pages/booking/Booking.css`
- Modify: `frontend/src/test/libraryPages.test.tsx`

**Interfaces:**
- Consumes: всё из Produces задачи 7; `ConfirmDialog` (`components/ConfirmDialog.tsx`: `open, title, message, confirmLabel?, onConfirm, onCancel`); `errorText` (`lib/errors`).
- Produces: `BookRating({ bookId, onRated })`, `BookComments({ bookId })`; `BookDialog` получает новый проп `onRated: () => void`.

- [ ] **Step 1: Падающие тесты** — в `frontend/src/test/libraryPages.test.tsx`.

Хелперы после `tile`:

```tsx
const dialog = () => document.querySelector('[role="dialog"]') as HTMLElement;

/** Ввод в управляемое React-поле: нативный setter + событие input. */
function typeInto(el: HTMLTextAreaElement, value: string): void {
  Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")?.set?.call(el, value);
  el.dispatchEvent(new Event("input", { bubbles: true }));
}

const NO_SOCIAL = {
  "GET /api/library/books/1/rating": { body: { avg: null, count: 0, mine: null } },
  "GET /api/library/books/2/rating": { body: { avg: null, count: 0, mine: null } },
  "GET /api/library/books/1/comments": { body: [] },
  "GET /api/library/books/2/comments": { body: [] },
};
```

В двух существующих тестах `Catalog` («бронь свободной книги…», «книгу увели…») добавить `...NO_SOCIAL,` первой строкой объекта `stubApi({ … })` — карточка теперь грузит оценки и обсуждение.

Новый `describe` в конец файла:

```tsx
describe("BookDialog: оценка и обсуждение", () => {
  const comments = [
    { id: 2, author_name: "Петров Пётр", text: "Чужой отзыв", created_at: "2026-10-06T09:00:00+00:00", mine: false },
    { id: 1, author_name: "Иванов Иван", text: "Мой отзыв", created_at: "2026-10-05T21:30:00+00:00", mine: true },
  ];

  it("звезда ставит оценку, средний обновляется, каталог перезапрашивается", async () => {
    const fetchMock = stubApi({
      ...NO_SOCIAL,
      "GET /api/library/genres": { body: [] },
      "GET /api/library/books": { body: [book(1, true)] },
      "PUT /api/library/books/1/rating": { body: { avg: 4, count: 1, mine: 4 } },
    });
    await mount(<Catalog />);
    await click(tile("Книга 1"));
    expect(dialog().textContent).toContain("Нет оценок");
    const catalogCalls = () =>
      fetchMock.mock.calls.filter(([url]) => url === "/api/library/books").length;
    const before = catalogCalls();

    await click(document.querySelector<HTMLElement>('[aria-label="Оценить на 4"]') ?? undefined);

    const put = fetchMock.mock.calls.find(([, init]) => (init as RequestInit | undefined)?.method === "PUT");
    expect(JSON.parse(String((put?.[1] as RequestInit).body))).toEqual({ score: 4 });
    expect(dialog().textContent).toContain("★ 4,0 · 1 оценка");
    expect(document.querySelector('[aria-label="Оценить на 4"]')?.getAttribute("aria-pressed")).toBe("true");
    expect(catalogCalls()).toBe(before + 1);
  });

  it("обсуждение: отправка, удаление своего, у чужого нет «Удалить»", async () => {
    const fetchMock = stubApi({
      "GET /api/library/genres": { body: [] },
      "GET /api/library/books/1/rating": { body: { avg: null, count: 0, mine: null } },
      "GET /api/library/books/1/comments": { body: comments },
      "POST /api/library/books/1/comments": { status: 201, body: comments[1] },
      "DELETE /api/library/comments/1": { status: 204, body: null },
      "GET /api/library/books": { body: [book(1, true)] },
    });
    await mount(<Catalog />);
    await click(tile("Книга 1"));

    expect(dialog().textContent).toContain("Обсуждение (2)");
    expect(dialog().textContent).toContain("06.10.2026"); // 21:30 UTC → МСК
    expect([...dialog().querySelectorAll("button")].filter((b) => b.textContent === "Удалить")).toHaveLength(1);

    const send = byText("Отправить") as HTMLButtonElement;
    expect(send.disabled).toBe(true);
    await act(async () => typeInto(dialog().querySelector("textarea") as HTMLTextAreaElement, "  Отлично  "));
    await click(byText("Отправить"));
    const post = fetchMock.mock.calls.find(([, init]) => (init as RequestInit | undefined)?.method === "POST");
    expect(JSON.parse(String((post?.[1] as RequestInit).body))).toEqual({ text: "Отлично" });

    await click([...dialog().querySelectorAll("button")].find((b) => b.textContent === "Удалить"));
    await click([...document.querySelectorAll("button")].filter((b) => b.textContent === "Удалить").pop());
    expect(fetchMock.mock.calls.some(([url, init]) => url === "/api/library/comments/1" && (init as RequestInit).method === "DELETE")).toBe(true);
  });

  it("Esc в подтверждении не закрывает карточку", async () => {
    stubApi({
      "GET /api/library/genres": { body: [] },
      "GET /api/library/books/1/rating": { body: { avg: null, count: 0, mine: null } },
      "GET /api/library/books/1/comments": { body: comments },
      "GET /api/library/books": { body: [book(1, true)] },
    });
    await mount(<Catalog />);
    await click(tile("Книга 1"));
    await click([...dialog().querySelectorAll("button")].find((b) => b.textContent === "Удалить"));
    expect(document.querySelectorAll('[role="dialog"]')).toHaveLength(2);

    await act(async () => window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" })));

    expect(document.querySelectorAll('[role="dialog"]')).toHaveLength(1);
    expect(dialog().textContent).toContain("Обсуждение");
  });
});
```

- [ ] **Step 2: Проверить падение** (пользователь)

Run: `cd frontend && npx vitest run src/test/libraryPages.test.tsx`
Expected: FAIL — нет «Нет оценок» / «Обсуждение» в карточке.

- [ ] **Step 3: `BookRating.tsx`**

```tsx
import { useEffect, useState } from "react";

import { formatRating, ratingsLabel } from "../../lib/bookingUi";
import { errorText } from "../../lib/errors";
import { getRating, rateBook } from "../../lib/library";
import type { Rating } from "../../lib/library";

const SCORES = [1, 2, 3, 4, 5];

type Props = {
  bookId: number;
  /** Оценка сохранена — каталог перезапрашивает список, чтобы обновилась плитка. */
  onRated: () => void;
};

/** Сводка оценок и «Ваша оценка» пятью звёздами-кнопками.
 *
 * Наведение подсвечивает звёзды до курсора, клик сразу сохраняет (PUT); ответ
 * сервиса — новая сводка. Ошибка показывается здесь же и не ломает карточку.
 */
export function BookRating({ bookId, onRated }: Props) {
  const [rating, setRating] = useState<Rating | null>(null);
  const [hover, setHover] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    getRating(bookId)
      .then((r) => alive && setRating(r))
      .catch((e: unknown) => alive && setError(errorText(e)));
    return () => {
      alive = false;
    };
  }, [bookId]);

  function rate(score: number): void {
    setBusy(true);
    setError(null);
    rateBook(bookId, score)
      .then((r) => {
        setRating(r);
        onRated();
      })
      .catch((e: unknown) => setError(errorText(e)))
      .finally(() => setBusy(false));
  }

  const shown = hover || rating?.mine || 0;
  let summary = "…";
  if (rating && rating.avg !== null) {
    summary = `★ ${formatRating(rating.avg)} · ${ratingsLabel(rating.count)}`;
  } else if (rating) {
    summary = "Нет оценок";
  }

  return (
    <div className="booking-rating">
      <span className="booking-rating__summary">{summary}</span>
      <span className="booking-rating__mine" onMouseLeave={() => setHover(0)}>
        Ваша оценка:
        {SCORES.map((n) => (
          <button
            key={n}
            type="button"
            className={`booking-star ${n <= shown ? "booking-star--on" : ""}`}
            aria-label={`Оценить на ${n}`}
            aria-pressed={rating?.mine === n}
            disabled={busy || rating === null}
            onMouseEnter={() => setHover(n)}
            onFocus={() => setHover(n)}
            onBlur={() => setHover(0)}
            onClick={() => rate(n)}
          >
            ★
          </button>
        ))}
      </span>
      {error && <p className="booking-error">{error}</p>}
    </div>
  );
}
```

- [ ] **Step 4: `BookComments.tsx`**

```tsx
import { useEffect, useState } from "react";

import { ConfirmDialog } from "../../components/ConfirmDialog";
import { formatMskDate } from "../../lib/bookingUi";
import { errorText } from "../../lib/errors";
import { addComment, COMMENT_MAX, deleteComment, listComments } from "../../lib/library";
import type { BookComment } from "../../lib/library";

/** Обсуждение книги: форма нового комментария и лента (новые сверху).
 *
 * Текст выводится только как текст (`pre-line`), без HTML. Удалить можно свой
 * комментарий — через подтверждение; после любого изменения лента перезапрашивается.
 */
export function BookComments({ bookId }: { bookId: number }) {
  const [rows, setRows] = useState<BookComment[] | null>(null);
  const [reloadKey, setReloadKey] = useState(0);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [toDelete, setToDelete] = useState<BookComment | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    listComments(bookId)
      .then((result) => alive && setRows(result))
      .catch((e: unknown) => alive && setError(errorText(e)));
    return () => {
      alive = false;
    };
  }, [bookId, reloadKey]);

  function run(action: () => Promise<unknown>, after?: () => void): void {
    setBusy(true);
    setError(null);
    action()
      .then(() => {
        after?.();
        setReloadKey((k) => k + 1);
      })
      .catch((e: unknown) => setError(errorText(e)))
      .finally(() => setBusy(false));
  }

  function send(): void {
    const text = draft.trim();
    if (!text) return;
    run(() => addComment(bookId, text), () => setDraft(""));
  }

  function confirmDelete(): void {
    if (!toDelete) return;
    const comment = toDelete;
    setToDelete(null);
    run(() => deleteComment(comment.id));
  }

  return (
    <section className="booking-comments">
      <h3 className="booking-comments__title">
        Обсуждение{rows !== null ? ` (${rows.length})` : ""}
      </h3>

      <textarea
        className="booking-comments__input"
        value={draft}
        maxLength={COMMENT_MAX}
        rows={3}
        placeholder="Поделитесь впечатлением…"
        onChange={(e) => setDraft(e.target.value)}
      />
      <div className="booking-comments__bar">
        <span className="booking-muted">
          {draft.length}/{COMMENT_MAX}
        </span>
        <button
          type="button"
          className="booking-btn booking-btn--primary"
          disabled={busy || !draft.trim()}
          onClick={send}
        >
          Отправить
        </button>
      </div>

      {error && <p className="booking-error">{error}</p>}
      {rows === null && !error && <p className="booking-muted">Загрузка…</p>}
      {rows !== null && rows.length === 0 && (
        <p className="booking-muted">Пока никто не написал — будьте первым.</p>
      )}

      <ul className="booking-comments__list">
        {(rows ?? []).map((c) => (
          <li key={c.id} className="booking-comment">
            <div className="booking-comment__head">
              <span className="booking-comment__author">{c.author_name}</span>
              <span className="booking-muted">{formatMskDate(c.created_at)}</span>
              {c.mine && (
                <button
                  type="button"
                  className="booking-link-btn booking-comment__delete"
                  disabled={busy}
                  onClick={() => setToDelete(c)}
                >
                  Удалить
                </button>
              )}
            </div>
            <p className="booking-comment__text">{c.text}</p>
          </li>
        ))}
      </ul>

      <ConfirmDialog
        open={toDelete !== null}
        title="Удалить комментарий?"
        message="Комментарий пропадёт из обсуждения."
        confirmLabel="Удалить"
        onConfirm={confirmDelete}
        onCancel={() => setToDelete(null)}
      />
    </section>
  );
}
```

- [ ] **Step 5: Встроить в `BookDialog.tsx`**

Импорты:

```tsx
import { BookComments } from "./BookComments";
import { BookRating } from "./BookRating";
```

Проп в `Props` и сигнатуре:

```tsx
type Props = {
  book: Book;
  busy: boolean;
  onLoan: (book: Book) => void;
  onRated: () => void;
  onClose: () => void;
};

export function BookDialog({ book, busy, onLoan, onRated, onClose }: Props) {
```

Обработчик Esc (Review Focus 4) — заменить тело `onKey`:

```tsx
    function onKey(event: KeyboardEvent): void {
      // Поверх открыт вложенный диалог (подтверждение удаления) — Esc закрывает
      // только его: он ловит то же событие сам.
      if (event.key !== "Escape") return;
      if (document.querySelectorAll('[role="dialog"]').length > 1) return;
      onClose();
    }
```

После `<p className="booking-book__line booking-muted">{book.genre}</p>`:

```tsx
        <BookRating bookId={book.id} onRated={onRated} />
```

После блока `<div className="booking-actions booking-book__actions">…</div>` (последний ребёнок диалога):

```tsx
        <BookComments bookId={book.id} />
```

Ширина — в CSS `.booking-book-dialog { width: min(40rem, 96vw); }` (было 34rem).

В `Catalog.tsx` — проп:

```tsx
        <BookDialog
          book={opened}
          busy={busyId === opened.id}
          onLoan={loan}
          onRated={() => setReloadKey((k) => k + 1)}
          onClose={() => setOpened(null)}
        />
```

- [ ] **Step 6: CSS** — в конец `frontend/src/pages/booking/Booking.css`:

```css
/* --- оценка и обсуждение в карточке книги ------------------------------- */

.booking-rating {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 0.5rem 1rem;
  margin: 0.6rem 0 0;
  font-size: 0.85rem;
}

.booking-rating__summary {
  color: #fde68a;
  font-weight: 700;
}

.booking-rating__mine {
  display: inline-flex;
  align-items: center;
  gap: 0.1rem;
  color: rgba(255, 255, 255, 0.52);
}

.booking-star {
  padding: 0 0.1rem;
  border: none;
  background: none;
  color: rgba(255, 255, 255, 0.22);
  font-size: 1.15rem;
  line-height: 1;
  cursor: pointer;
  transition: color 0.1s;
}

.booking-star--on {
  color: #fbbf24;
}

.booking-star:disabled {
  cursor: default;
}

.booking-star:focus-visible {
  outline: 2px solid rgba(0, 199, 177, 0.6);
  border-radius: 4px;
}

.booking-comments {
  margin-top: 1.25rem;
  padding-top: 1rem;
  border-top: 1px solid rgba(255, 255, 255, 0.08);
}

.booking-comments__title {
  margin: 0 0 0.6rem;
  color: #fff;
  font-size: 0.95rem;
  font-weight: 700;
}

.booking-comments__input {
  box-sizing: border-box;
  width: 100%;
  resize: vertical;
  border-radius: 12px;
  border: 1px solid rgba(255, 255, 255, 0.1);
  background: rgba(0, 0, 0, 0.35);
  padding: 0.55rem 0.7rem;
  color: #fff;
  font: inherit;
  font-size: 0.85rem;
  outline: none;
}

.booking-comments__input:focus {
  border-color: rgba(0, 199, 177, 0.45);
  box-shadow: 0 0 0 2px rgba(0, 199, 177, 0.15);
}

.booking-comments__bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin: 0.4rem 0 0.75rem;
}

.booking-comments__list {
  display: flex;
  flex-direction: column;
  gap: 0.6rem;
  margin: 0;
  padding: 0;
  list-style: none;
}

.booking-comment {
  padding: 0.6rem 0.75rem;
  border: 1px solid rgba(255, 255, 255, 0.06);
  border-radius: 12px;
  background: rgba(0, 0, 0, 0.2);
}

.booking-comment__head {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 0.5rem;
  margin-bottom: 0.3rem;
  font-size: 0.78rem;
}

.booking-comment__author {
  color: #fff;
  font-weight: 700;
}

.booking-comment__delete {
  margin-left: auto;
}

.booking-comment__text {
  margin: 0;
  font-size: 0.85rem;
  line-height: 1.45;
  white-space: pre-line;
  overflow-wrap: anywhere;
}
```

- [ ] **Step 7: eslint + tsc** (исполнитель)

Run: `cd frontend && npx tsc --noEmit && npx eslint src`
Expected: чисто.

- [ ] **Step 8: Тесты и сборка** (пользователь)

Run: `cd frontend && npx vitest run && npm run build`
Expected: PASS, сборка успешна.

- [ ] **Step 9: Ручная проверка в браузере** (пользователь, на поднятой цепочке): поставить/сменить оценку — плитка обновилась; длинный комментарий с переносами строк отображается текстом; Esc в подтверждении закрывает только его.

- [ ] **Step 10: Предложить коммит (`pa_bff`, задачи 6–8)**

```
feat(library): rate books and discuss them in the book card

- proxy rating and comments endpoints of pa_booking_service
- average rating in catalog tiles
- star rating and discussion block in the book card
```

---

### Task 9: Документация (pa_booking_service)

**Files:**
- Modify: `docs/superpowers/specs/2026-10-08-library-ratings-comments-design.md` (строка статуса)
- Modify: `docs/superpowers/specs/2026-10-02-booking-service-design.md` §4.2 (ссылка)

- [ ] **Step 1:** В новой спеке: `Статус: черновик на ревью.` → `Статус: реализована (2026-10-08).`
- [ ] **Step 2:** В основной спеке в конце §4.2, после таблицы правил, добавить строку:

```markdown
Оценки и обсуждения книг (таблицы `library_ratings`, `library_comments`) — спека
`2026-10-08-library-ratings-comments-design.md`.
```

- [ ] **Step 3: Предложить коммит**

`docs(library): mark ratings and comments spec implemented`
