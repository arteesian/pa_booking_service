from __future__ import annotations

import uuid
from datetime import date, time

import pytest

from pa_booking.domain.appointments import Kind
from pa_booking.migrate.bots import (
    Assignment,
    BookRow,
    SlotRow,
    clean_book,
    parse_assign,
    parse_slot,
    plan_slots,
)

TODAY = date(2026, 10, 6)
HUID = "aaaaaaaa-0000-0000-0000-000000000001"
EMP = uuid.UUID("11111111-1111-1111-1111-111111111111")


def row(d: str, t: str = "16:00", *, huid: str | None = None, tg_id: int | None = None) -> SlotRow:
    return SlotRow(date_str=d, time_str=t, huid=huid, tg_id=tg_id, comment="психолог")


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


def test_parse_assign() -> None:
    assert parse_assign(f"{HUID}={EMP}:mkr") == Assignment(HUID, EMP, Kind.MKR)
    for bad in (f"{HUID}={EMP}", f"{HUID}=not-uuid:psy", f"{HUID}={EMP}:other", "x"):
        with pytest.raises(ValueError):
            parse_assign(bad)


def test_plan_keeps_future_only_and_splits_free_and_booked() -> None:
    plan = plan_slots(
        [
            row("05.10.2026"),  # вчера — не переносим
            row("06.10.2026", "10:00"),  # сегодня — переносим
            row("07.10.2026", huid=HUID),
            row("08.10.2026", tg_id=123),  # Telegram-бронь — тоже занят
        ],
        today=TODAY,
        assignments=[Assignment(HUID, EMP, Kind.PSY)],
    )

    assert plan.free == ((date(2026, 10, 6), time(10, 0)),)
    assert [(b.slot_date, b.assignment) for b in plan.booked] == [
        (date(2026, 10, 7), Assignment(HUID, EMP, Kind.PSY)),
        (date(2026, 10, 8), None),
    ]
    assert [b.slot_date for b in plan.unassigned] == [date(2026, 10, 8)]


def test_plan_reports_bad_row_number() -> None:
    with pytest.raises(ValueError, match="строка 2"):
        plan_slots([row("06.10.2026"), row("06/10/2026")], today=TODAY, assignments=[])


def test_assign_for_unknown_huid_is_an_error() -> None:
    """Опечатка в HUID не должна молча оставить бронь без переноса."""
    with pytest.raises(ValueError, match="не найден"):
        plan_slots(
            [row("07.10.2026", huid=HUID)],
            today=TODAY,
            assignments=[Assignment("bbbbbbbb-0000-0000-0000-000000000002", EMP, Kind.PSY)],
        )


def test_clean_book_strips_and_rejects_empty() -> None:
    assert clean_book(BookRow(" Роман ", "Булгаков", " Мастер ", "—")) == BookRow(
        "Роман", "Булгаков", "Мастер", "—"
    )
    with pytest.raises(ValueError):
        clean_book(BookRow("Роман", "  ", "Мастер", "—"))
