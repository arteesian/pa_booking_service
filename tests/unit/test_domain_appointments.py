from __future__ import annotations

from datetime import UTC, date, datetime, time

import pytest

from pa_booking.domain.appointments import (
    MSK,
    CancelEffect,
    DeleteEffect,
    Kind,
    SlotState,
    cancel_effect,
    cleanup_date,
    delete_effect,
    ensure_bookable,
    is_upcoming,
    month_bounds,
    overview_range,
    text_booked,
    text_cancelled_by_user,
    text_cleanup,
    text_slot_removed,
    validate_new_slots,
)
from pa_booking.domain.errors import DomainError

D = date(2026, 10, 5)


def msk(h: int, m: int = 0, d: date = D) -> datetime:
    return datetime(d.year, d.month, d.day, h, m, tzinfo=MSK)


def slot(
    t: time = time(16, 0), d: date = D, *, removed: bool = False, booked: bool = False
) -> SlotState:
    return SlotState(slot_date=d, slot_time=t, removed=removed, booked=booked)


def code_of(exc: pytest.ExceptionInfo[DomainError]) -> str:
    return exc.value.code


# --- запись ---


def test_free_future_slot_is_bookable_below_limit() -> None:
    ensure_bookable(slot(), active_in_month=3, now=msk(10))


def test_fourth_active_booking_in_month_hits_limit() -> None:
    with pytest.raises(DomainError) as exc:
        ensure_bookable(slot(), active_in_month=4, now=msk(10))
    assert code_of(exc) == "monthly_limit"


@pytest.mark.parametrize(
    "state",
    [slot(removed=True), slot(booked=True), slot(t=time(10, 0))],
    ids=["removed", "booked", "started"],
)
def test_unavailable_slots(state: SlotState) -> None:
    with pytest.raises(DomainError) as exc:
        ensure_bookable(state, active_in_month=0, now=msk(10))
    assert code_of(exc) == "slot_unavailable"


def test_slot_starting_exactly_now_is_not_bookable() -> None:
    with pytest.raises(DomainError):
        ensure_bookable(slot(t=time(16, 0)), active_in_month=0, now=msk(16))


def test_unavailable_checked_before_limit() -> None:
    """Занятый слот — «недоступен», даже если лимит тоже исчерпан."""
    with pytest.raises(DomainError) as exc:
        ensure_bookable(slot(booked=True), active_in_month=4, now=msk(10))
    assert code_of(exc) == "slot_unavailable"


# --- отмена пользователем (Д-2, замок 14:00) ---


def test_cancel_today_before_14_frees_slot() -> None:
    assert cancel_effect(D, time(16, 0), now=msk(13, 59)) is CancelEffect.FREE_SLOT


def test_cancel_today_at_14_removes_slot() -> None:
    assert cancel_effect(D, time(16, 0), now=msk(14, 0)) is CancelEffect.REMOVE_SLOT


def test_lock_uses_moscow_time_not_utc() -> None:
    """11:00 UTC = 14:00 МСК — замок уже действует."""
    now = datetime(2026, 10, 5, 11, 0, tzinfo=UTC)
    assert cancel_effect(D, time(16, 0), now=now) is CancelEffect.REMOVE_SLOT


def test_cancel_tomorrow_after_14_frees_slot() -> None:
    tomorrow = date(2026, 10, 6)
    assert cancel_effect(tomorrow, time(16, 0), now=msk(15)) is CancelEffect.FREE_SLOT


def test_cancel_after_start_is_too_late() -> None:
    with pytest.raises(DomainError) as exc:
        cancel_effect(D, time(16, 0), now=msk(16, 0))
    assert code_of(exc) == "too_late_to_cancel"


# --- слоты специалиста (Д-8, Д-9) ---


def test_new_slots_in_past_date_rejected() -> None:
    with pytest.raises(DomainError) as exc:
        validate_new_slots(date(2026, 10, 4), [time(16, 0)], now=msk(10))
    assert code_of(exc) == "slot_date_in_past"


def test_new_slots_today_after_14_allowed_and_deduplicated() -> None:
    times = validate_new_slots(D, [time(19, 0), time(16, 0), time(19, 0)], now=msk(15))
    assert times == (time(16, 0), time(19, 0))


def test_delete_free_slot_even_in_past() -> None:
    assert delete_effect(slot(t=time(10, 0)), now=msk(12)) is DeleteEffect.REMOVE_FREE


def test_delete_booked_future_slot_cancels_booking() -> None:
    assert delete_effect(slot(booked=True), now=msk(12)) is DeleteEffect.REMOVE_AND_CANCEL


def test_delete_booked_started_slot_refused() -> None:
    with pytest.raises(DomainError) as exc:
        delete_effect(slot(t=time(10, 0), booked=True), now=msk(12))
    assert code_of(exc) == "slot_in_past"


# --- чистка 14:00 и календарь ---


def test_cleanup_not_due_before_14() -> None:
    assert cleanup_date(msk(13, 59)) is None


def test_cleanup_due_from_14_for_today_in_moscow() -> None:
    assert cleanup_date(msk(14, 0)) == D
    # 22:30 UTC 5-го — уже 01:30 МСК 6-го: до 14:00 нового дня чистка не нужна.
    assert cleanup_date(datetime(2026, 10, 5, 22, 30, tzinfo=UTC)) is None


def test_month_bounds_handle_february_and_december() -> None:
    assert month_bounds(2027, 2) == (date(2027, 2, 1), date(2027, 2, 28))
    assert month_bounds(2026, 12) == (date(2026, 12, 1), date(2026, 12, 31))


def test_overview_is_month_plus_minus_three_days() -> None:
    assert overview_range(2027, 3) == (date(2027, 2, 26), date(2027, 4, 3))


def test_upcoming_includes_slot_starting_now() -> None:
    assert is_upcoming(D, time(16, 0), now=msk(16, 0))
    assert not is_upcoming(D, time(16, 0), now=msk(16, 1))


# --- тексты §5.5 ---


def test_notification_texts() -> None:
    assert text_booked("Иванов Иван", D, time(16, 0), Kind.MKR) == (
        "✅ Иванов Иван\nЗаписался на 05.10.2026 в 16:00\nКонсультация МКР"
    )
    assert text_cancelled_by_user("Иванов Иван", D, time(9, 30), Kind.PSY) == (
        "❌ Иванов Иван\nОтменил запись на 05.10.2026 в 09:30\nПсихолог"
    )
    assert text_slot_removed("Иванов Иван", D, time(16, 0)) == (
        "Слот 05.10.2026 — 16:00 был удалён, запись Иванов Иван отменена"
    )
    assert text_cleanup([time(17, 0), time(16, 0)]) == (
        "Свободные окна на сегодня 16:00, 17:00 были удалены"
    )
