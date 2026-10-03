from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from pa_booking.domain.errors import DomainError
from pa_booking.domain.library import (
    MSK,
    ensure_loanable,
    ensure_removable,
    extended_due,
    is_overdue,
    new_loan_dates,
    text_extended,
    text_loaned,
    text_returned,
)

DUE = date(2026, 10, 12)


def msk(d: date, h: int = 10) -> datetime:
    return datetime(d.year, d.month, d.day, h, tzinfo=MSK)


def test_loan_dates_use_moscow_today() -> None:
    """23:30 UTC 4-го — уже 02:30 МСК 5-го."""
    now = datetime(2026, 10, 4, 23, 30, tzinfo=UTC)
    assert new_loan_dates(now=now) == (date(2026, 10, 5), date(2026, 10, 12))


def test_book_on_loan_is_unavailable() -> None:
    ensure_loanable(on_loan=False)
    with pytest.raises(DomainError) as exc:
        ensure_loanable(on_loan=True)
    assert exc.value.code == "book_unavailable"


def test_extend_on_due_day_adds_week_to_due_not_today() -> None:
    assert extended_due(DUE, now=msk(DUE, 23)) == date(2026, 10, 19)
    assert extended_due(DUE, now=msk(date(2026, 10, 6))) == date(2026, 10, 19)


def test_extend_overdue_refused() -> None:
    with pytest.raises(DomainError) as exc:
        extended_due(DUE, now=msk(date(2026, 10, 13), 0))
    assert exc.value.code == "loan_overdue"


def test_overdue_starts_day_after_due() -> None:
    assert not is_overdue(DUE, now=msk(DUE, 23))
    assert is_overdue(DUE, now=msk(date(2026, 10, 13), 0))


def test_book_on_loan_cannot_be_removed() -> None:
    ensure_removable(on_loan=False)
    with pytest.raises(DomainError) as exc:
        ensure_removable(on_loan=True)
    assert exc.value.code == "book_on_loan"


def test_notification_texts() -> None:
    title = "Мастер и Маргарита"
    assert text_loaned("Иванов Иван", title, DUE) == (
        "Иванов Иван забронировал книгу Мастер и Маргарита ⏳\nДата возврата: 12.10.2026"
    )
    assert text_extended("Иванов Иван", title, date(2026, 10, 19)) == (
        "Иванов Иван продлил бронирование: Мастер и Маргарита ✅\nНовая дата возврата: 19.10.2026"
    )
    assert text_returned("Иванов Иван", title, by_librarian=False) == (
        "Иванов Иван вернул книгу Мастер и Маргарита ✅"
    )
    assert text_returned("Иванов Иван", title, by_librarian=True) == (
        "Иванов Иван вернул книгу Мастер и Маргарита ✅ (отметил библиотекарь)"
    )
