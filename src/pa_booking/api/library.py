"""Ручки библиотеки (спека §5.2).

Пользователь — HUID в обоих каналах (§3.1): ``User`` — смотреть, ``Me`` — личные
действия (нужен HUID), ``Librarian`` — админка, только из бота.

Порядок в меняющих ручках — как в записях: блокировка строки → правило домена →
изменение → ``commit`` → уведомление фоновой задачей.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Response, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from pa_booking.api.deps import (
    DbSession,
    Identified,
    Now,
    Principal,
    admin,
    identified,
    principal,
)
from pa_booking.api.library_schemas import (
    AdminLoanOut,
    BookCreate,
    BookOut,
    BookUpdate,
    LoanOut,
)
from pa_booking.api.schemas import MeOut
from pa_booking.core.config import Settings, get_settings
from pa_booking.core.errors import ApiError
from pa_booking.db import library as repo
from pa_booking.db.directory import display_names
from pa_booking.db.models import LibraryBook, LibraryLoan
from pa_booking.domain.identity import Module
from pa_booking.domain.library import (
    ensure_loanable,
    ensure_removable,
    extended_due,
    is_overdue,
    new_loan_dates,
    text_extended,
    text_loaned,
    text_returned,
)
from pa_booking.domain.moscow import MSK, today_msk
from pa_booking.export.xlsx import build_xlsx
from pa_booking.notify.botx import Notifier, make_notifier, notify_safely

MODULE: Module = "library"
XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
EXPORT_HEADERS = ["Книга", "ФИО", "Начало", "Срок", "Дата возврата", "Кто отметил"]

router = APIRouter(prefix="/library", tags=["library"])


def get_library_notifier(
    settings: Annotated[Settings, Depends(get_settings)],
) -> Notifier | None:
    """Notifier библиотеки; None — BotX не настроен (сбой посчитает notify_safely)."""
    return make_notifier(settings, MODULE)


LibraryNotifier = Annotated[Notifier | None, Depends(get_library_notifier)]
User = Annotated[Principal, Depends(principal(MODULE))]
Me = Annotated[Identified, Depends(identified(MODULE))]
Librarian = Annotated[Identified, Depends(admin(MODULE))]


class LoanState(StrEnum):
    ACTIVE = "active"
    OVERDUE = "overdue"


def _book_out(book: LibraryBook, *, available: bool) -> BookOut:
    return BookOut(
        id=book.id,
        genre=book.genre,
        author=book.author,
        title=book.title,
        description=book.description,
        available=available,
    )


def _loan_out(loan: LibraryLoan, book: LibraryBook, *, now: datetime) -> LoanOut:
    open_ = loan.returned_at is None
    return LoanOut(
        id=loan.id,
        book=_book_out(book, available=not open_),
        starts_on=loan.starts_on,
        due_on=loan.due_on,
        overdue=open_ and is_overdue(loan.due_on, now=now),
        returned_at=loan.returned_at,
    )


def _admin_loan_out(
    loan: LibraryLoan, book: LibraryBook, full_name: str, *, now: datetime
) -> AdminLoanOut:
    return AdminLoanOut(
        **_loan_out(loan, book, now=now).model_dump(),
        user_huid=loan.user_huid,
        full_name=full_name,
        returned_by_librarian=loan.returned_by_librarian,
    )


def _live_book(session: Session, book_id: int) -> repo.BookWithLoan:
    locked = repo.lock_book(session, book_id)
    if locked is None or locked.book.removed_at is not None:
        raise ApiError(404, "not_found", "Книга не найдена")
    return locked


def _name(session: Session, loan: LibraryLoan) -> str:
    return display_names(session, [(loan.user_huid, loan.user_name)])[loan.user_huid]


def _names(session: Session, rows: list[repo.LoanWithBook]) -> dict[uuid.UUID, str]:
    return display_names(session, [(r.loan.user_huid, r.loan.user_name) for r in rows])


# --- пользователь ---


@router.get("/me", response_model=MeOut)
def me(user: User) -> MeOut:
    return MeOut(huid=user.huid, roles=sorted(user.roles))


@router.get("/genres", response_model=list[str])
def list_genres(session: DbSession, _user: User) -> list[str]:
    return repo.genres(session)


@router.get("/books", response_model=list[BookOut])
def list_books(session: DbSession, _user: User, genre: str | None = None) -> list[BookOut]:
    """Каталог: сначала свободные, потом по названию."""
    return [_book_out(r.book, available=not r.on_loan) for r in repo.catalog(session, genre)]


@router.post("/books/{book_id}/loan", status_code=status.HTTP_201_CREATED, response_model=LoanOut)
def loan_book(
    book_id: int,
    session: DbSession,
    user: Me,
    now: Now,
    notifier: LibraryNotifier,
    background: BackgroundTasks,
) -> LoanOut:
    locked = _live_book(session, book_id)
    ensure_loanable(on_loan=locked.on_loan)
    starts_on, due_on = new_loan_dates(now=now)
    loan = LibraryLoan(
        book_id=locked.book.id,
        user_huid=user.huid,
        user_name=user.name,
        channel=user.channel,
        starts_on=starts_on,
        due_on=due_on,
        created_at=now,
    )
    session.add(loan)
    try:
        session.commit()
    except IntegrityError as exc:
        # Страховка: блокировка книги сюда не пускает, но индекс — последнее слово.
        session.rollback()
        raise ApiError(409, "book_unavailable", "Книга уже забронирована") from exc

    text = text_loaned(_name(session, loan), locked.book.title, due_on)
    background.add_task(notify_safely, notifier, text, module=MODULE)
    return _loan_out(loan, locked.book, now=now)


@router.get("/loans/my", response_model=list[LoanOut])
def my_loans(session: DbSession, user: Me, now: Now) -> list[LoanOut]:
    """Невозвращённые выдачи из обоих каналов, просроченные помечены (Д-3)."""
    return [_loan_out(r.loan, r.book, now=now) for r in repo.my_loans(session, user.huid)]


def _own_open_loan(session: Session, loan_id: int, huid: uuid.UUID) -> repo.LoanWithBook:
    locked = repo.lock_loan(session, loan_id)
    # Чужая или уже возвращённая — 404: существование чужого не раскрываем.
    if locked is None or locked.loan.user_huid != huid or locked.loan.returned_at is not None:
        raise ApiError(404, "not_found", "Бронь не найдена")
    return locked


@router.post("/loans/{loan_id}/extend", response_model=LoanOut)
def extend_loan(
    loan_id: int,
    session: DbSession,
    user: Me,
    now: Now,
    notifier: LibraryNotifier,
    background: BackgroundTasks,
) -> LoanOut:
    locked = _own_open_loan(session, loan_id, user.huid)
    locked.loan.due_on = extended_due(locked.loan.due_on, now=now)
    session.commit()

    text = text_extended(_name(session, locked.loan), locked.book.title, locked.loan.due_on)
    background.add_task(notify_safely, notifier, text, module=MODULE)
    return _loan_out(locked.loan, locked.book, now=now)


@router.post("/loans/{loan_id}/return", response_model=LoanOut)
def return_loan(
    loan_id: int,
    session: DbSession,
    user: Me,
    now: Now,
    notifier: LibraryNotifier,
    background: BackgroundTasks,
) -> LoanOut:
    """Возврат своей выдачи, в т.ч. просроченной (Д-3)."""
    locked = _own_open_loan(session, loan_id, user.huid)
    locked.loan.returned_at = now
    session.commit()

    text = text_returned(_name(session, locked.loan), locked.book.title, by_librarian=False)
    background.add_task(notify_safely, notifier, text, module=MODULE)
    return _loan_out(locked.loan, locked.book, now=now)


# --- библиотекарь ---


@router.post("/admin/books", status_code=status.HTTP_201_CREATED, response_model=BookOut)
def admin_add_book(body: BookCreate, session: DbSession, _admin: Librarian, now: Now) -> BookOut:
    book = LibraryBook(**body.model_dump(), created_at=now)
    session.add(book)
    session.commit()
    return _book_out(book, available=True)


@router.patch("/admin/books/{book_id}", response_model=BookOut)
def admin_update_book(
    book_id: int, body: BookUpdate, session: DbSession, _admin: Librarian
) -> BookOut:
    """Изменить можно и книгу на руках — меняется только описание каталога."""
    locked = _live_book(session, book_id)
    for field, value in body.model_dump(exclude_none=True).items():
        setattr(locked.book, field, value)
    session.commit()
    return _book_out(locked.book, available=not locked.on_loan)


@router.delete("/admin/books/{book_id}", status_code=status.HTTP_204_NO_CONTENT)
def admin_remove_book(book_id: int, session: DbSession, _admin: Librarian, now: Now) -> Response:
    """Мягкое удаление; книгу на руках удалить нельзя (409 ``book_on_loan``)."""
    locked = _live_book(session, book_id)
    ensure_removable(on_loan=locked.on_loan)
    locked.book.removed_at = now
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/admin/loans", response_model=list[AdminLoanOut])
def admin_loans(
    session: DbSession,
    _admin: Librarian,
    now: Now,
    state: Annotated[LoanState, Query()],
) -> list[AdminLoanOut]:
    rows = repo.open_loans(session, overdue=state is LoanState.OVERDUE, today=today_msk(now))
    names = _names(session, rows)
    return [_admin_loan_out(r.loan, r.book, names[r.loan.user_huid], now=now) for r in rows]


@router.post("/admin/loans/{loan_id}/return", response_model=AdminLoanOut)
def admin_return_loan(
    loan_id: int,
    session: DbSession,
    _admin: Librarian,
    now: Now,
    notifier: LibraryNotifier,
    background: BackgroundTasks,
) -> AdminLoanOut:
    """Библиотекарь отмечает возврат любой невозвращённой выдачи (Д-4)."""
    locked = repo.lock_loan(session, loan_id)
    if locked is None or locked.loan.returned_at is not None:
        raise ApiError(404, "not_found", "Бронь не найдена")
    locked.loan.returned_at = now
    locked.loan.returned_by_librarian = True
    session.commit()

    name = _name(session, locked.loan)
    text = text_returned(name, locked.book.title, by_librarian=True)
    background.add_task(notify_safely, notifier, text, module=MODULE)
    return _admin_loan_out(locked.loan, locked.book, name, now=now)


@router.get("/admin/export", response_class=Response)
def admin_export(session: DbSession, _admin: Librarian) -> Response:
    """xlsx истории выдач (замена выгрузки ``all_reserv_book`` бота)."""
    rows = repo.history(session)
    names = _names(session, rows)

    def who(loan: LibraryLoan) -> str | None:
        if loan.returned_at is None:
            return None
        return "Библиотекарь" if loan.returned_by_librarian else "Пользователь"

    data = build_xlsx(
        "История выдач",
        EXPORT_HEADERS,
        (
            (
                r.book.title,
                names[r.loan.user_huid],
                r.loan.starts_on,
                r.loan.due_on,
                None if r.loan.returned_at is None else r.loan.returned_at.astimezone(MSK).date(),
                who(r.loan),
            )
            for r in rows
        ),
    )
    return Response(
        content=data,
        media_type=XLSX_MEDIA_TYPE,
        headers={"Content-Disposition": "attachment; filename=library_history.xlsx"},
    )
