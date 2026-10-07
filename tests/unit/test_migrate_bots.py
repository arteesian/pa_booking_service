from __future__ import annotations

import uuid
from datetime import date, time

import pytest

from pa_booking.domain.appointments import Kind
from pa_booking.migrate.bots import (
    BookLoan,
    BookRow,
    SlotRow,
    TelegramSlot,
    kind_from_comment,
    parse_kind,
    parse_slot,
    plan_books,
    plan_slots,
)

TODAY = date(2026, 10, 6)
HUID = uuid.UUID("aaaaaaaa-0000-0000-0000-000000000001")


def row(
    d: str,
    t: str = "16:00",
    *,
    huid: str | None = None,
    tg_id: int | None = None,
    comment: str | None = "психолог",
) -> SlotRow:
    return SlotRow(date_str=d, time_str=t, huid=huid, tg_id=tg_id, comment=comment)


def test_parse_slot_bot_format() -> None:
    assert parse_slot("05.10.2026", "09:30") == (date(2026, 10, 5), time(9, 30))


@pytest.mark.parametrize(
    ("d", "t"),
    [
        ("2026-10-05", "09:30"),
        ("5.10.2026", "09:30"),
        ("31.02.2026", "09:30"),
        ("05.10.2026", "9:30"),
        ("05.10.2026", "24:00"),
    ],
)
def test_parse_slot_rejects_other_formats(d: str, t: str) -> None:
    with pytest.raises(ValueError):
        parse_slot(d, t)


def test_plan_keeps_future_only_and_splits_free_booked_telegram() -> None:
    plan = plan_slots(
        [
            row("05.10.2026"),  # вчера — не переносим
            row("06.10.2026", "10:00"),  # сегодня — переносим
            row("07.10.2026", huid=str(HUID).upper()),  # регистр HUID в боте — любой
            row("08.10.2026", tg_id=123),  # Telegram-бронь — не переносим, в отчёт
        ],
        today=TODAY,
    )

    assert plan.free == ((date(2026, 10, 6), time(10, 0)),)
    assert [(b.slot_date, b.huid, b.kind) for b in plan.booked] == [
        (date(2026, 10, 7), HUID, Kind.PSY)
    ]
    assert plan.telegram == (TelegramSlot(date(2026, 10, 8), time(16, 0), 123),)
    assert plan.unresolved == ()


def test_plan_reports_bad_row_number() -> None:
    with pytest.raises(ValueError, match="строка 2"):
        plan_slots([row("06.10.2026"), row("06/10/2026")], today=TODAY)
    with pytest.raises(ValueError, match="строка 1: не HUID"):
        plan_slots([row("07.10.2026", huid="не-huid")], today=TODAY)


@pytest.mark.parametrize(
    ("comment", "kind"),
    [
        ("Психолог", Kind.PSY),
        ("к психологу, пожалуйста", Kind.PSY),
        ("МКР", Kind.MKR),
        ("консультация мкр", Kind.MKR),
        ("психолог или мкр?", None),
        ("к Анне", None),
        ("", None),
        (None, None),
    ],
)
def test_kind_from_comment_only_when_unambiguous(comment: str | None, kind: Kind | None) -> None:
    assert kind_from_comment(comment) is kind


def test_unclear_comment_needs_kind_flag() -> None:
    plan = plan_slots([row("07.10.2026", huid=str(HUID), comment="к Анне")], today=TODAY)
    (b,) = plan.unresolved
    assert "нужен --kind" in b.source


def test_explicit_kind_overrides_comment() -> None:
    plan = plan_slots(
        [row("07.10.2026", huid=str(HUID), comment="МКР")], today=TODAY, kinds={HUID: Kind.PSY}
    )
    assert (plan.booked[0].kind, plan.booked[0].source) == (Kind.PSY, "тип из --kind")


def test_kind_for_unknown_huid_is_an_error() -> None:
    """Опечатка в HUID не должна молча оставить бронь без типа."""
    with pytest.raises(ValueError, match="--kind"):
        plan_slots([row("07.10.2026", huid=str(HUID))], today=TODAY, kinds={uuid.uuid4(): Kind.PSY})


def test_parse_kind() -> None:
    assert parse_kind(f"{HUID}=mkr") == (HUID, Kind.MKR)
    for bad in (f"{HUID}=other", "не-huid=psy", "x"):
        with pytest.raises(ValueError):
            parse_kind(bad)


# --- книги ---


def test_plan_books_strips_and_rejects_empty() -> None:
    plan = plan_books([BookRow(" Роман ", "Булгаков", " Мастер ", "—")])
    (b,) = plan.books
    assert (b.genre, b.title, b.loan) == ("Роман", "Мастер", None)
    with pytest.raises(ValueError, match="пустые поля author"):
        plan_books([BookRow("Роман", "  ", "Мастер", "—")])


def test_plan_books_moves_loans_by_huid_and_reports_telegram() -> None:
    start, end = date(2026, 9, 20), date(2026, 9, 27)  # просрочена — всё равно переносим (Д-3)
    plan = plan_books(
        [
            BookRow("Роман", "Булгаков", "Мастер", "—", huid=str(HUID), start=start, end=end),
            BookRow("Роман", "Толстой", "Война и мир", "—", tg_id=42, start=start, end=end),
            BookRow("Поэзия", "Пушкин", "Онегин", "—"),
        ]
    )
    assert [b.loan for b in plan.books] == [BookLoan(HUID, start, end), None, None]
    assert plan.telegram == ("Война и мир",)
    assert plan.loans == 1


def test_loan_without_dates_is_an_error() -> None:
    with pytest.raises(ValueError, match="нет дат"):
        plan_books([BookRow("Роман", "Булгаков", "Мастер", "—", huid=str(HUID))])
