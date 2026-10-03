"""Правила библиотеки (спека §4.2).

Чистый модуль: без БД и HTTP. ``now`` — aware datetime в любой зоне; «сегодня» —
дата по Москве. Как в боте: бронь на 7 дней, продление +7 от текущего срока, без
лимитов.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from pa_booking.domain.errors import DomainError
from pa_booking.domain.moscow import MSK as MSK
from pa_booking.domain.moscow import today_msk

LOAN_DAYS = timedelta(days=7)


def new_loan_dates(*, now: datetime) -> tuple[date, date]:
    """(``starts_on``, ``due_on``) новой выдачи."""
    today = today_msk(now)
    return today, today + LOAN_DAYS


def ensure_loanable(*, on_loan: bool) -> None:
    if on_loan:
        raise DomainError("book_unavailable", "Книга уже забронирована")


def is_overdue(due_on: date, *, now: datetime) -> bool:
    """Просрочена со следующего дня после срока; в день срока — ещё нет."""
    return due_on < today_msk(now)


def extended_due(due_on: date, *, now: datetime) -> date:
    """Новый срок при продлении: +7 от текущего срока. Просроченную не продлеваем."""
    if is_overdue(due_on, now=now):
        raise DomainError("loan_overdue", "Срок брони истёк — продлить нельзя, верните книгу")
    return due_on + LOAN_DAYS


def ensure_removable(*, on_loan: bool) -> None:
    if on_loan:
        raise DomainError("book_on_loan", "Книга на руках — удалить нельзя")


def _d(value: date) -> str:
    return value.strftime("%d.%m.%Y")


def text_loaned(name: str, title: str, due_on: date) -> str:
    return f"{name} забронировал книгу {title} ⏳\nДата возврата: {_d(due_on)}"


def text_extended(name: str, title: str, due_on: date) -> str:
    return f"{name} продлил бронирование: {title} ✅\nНовая дата возврата: {_d(due_on)}"


def text_returned(name: str, title: str, *, by_librarian: bool) -> str:
    mark = " (отметил библиотекарь)" if by_librarian else ""
    return f"{name} вернул книгу {title} ✅{mark}"
