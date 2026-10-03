"""Правила записей к психологу и на консультацию МКР (спека §4.1).

Чистый модуль: без БД и HTTP. Текущее время — параметр ``now`` (aware datetime в любой
зоне); «сегодня», 14:00 и границы месяца считаются по Москве. Слот хранит дату и
время по МСК без зоны.
"""

from __future__ import annotations

import calendar
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from enum import StrEnum

from pa_booking.domain.errors import DomainError

# «as MSK» — явный реэкспорт: модуль записей и его тесты берут MSK отсюда.
from pa_booking.domain.moscow import MSK as MSK

# С 14:00 МСК свободные слоты на сегодня удаляются, а отмена записи на сегодня
# удаляет слот, а не освобождает его (как в боте: schedule_utils.py:33-40).
LOCK_TIME = time(14, 0)
MONTHLY_LIMIT = 4
OVERVIEW_MARGIN = timedelta(days=3)


class Kind(StrEnum):
    PSY = "psy"
    MKR = "mkr"


KIND_TITLES: dict[Kind, str] = {Kind.PSY: "Психолог", Kind.MKR: "Консультация МКР"}


class BookingStatus(StrEnum):
    ACTIVE = "active"
    CANCELLED_BY_USER = "cancelled_by_user"
    CANCELLED_BY_SPECIALIST = "cancelled_by_specialist"


STATUS_TITLES: dict[BookingStatus, str] = {
    BookingStatus.ACTIVE: "Активна",
    BookingStatus.CANCELLED_BY_USER: "Отменена пользователем",
    BookingStatus.CANCELLED_BY_SPECIALIST: "Отменена специалистом",
}


@dataclass(frozen=True)
class SlotState:
    slot_date: date
    slot_time: time
    removed: bool
    booked: bool  # есть активная бронь


class CancelEffect(StrEnum):
    FREE_SLOT = "free_slot"
    REMOVE_SLOT = "remove_slot"


class DeleteEffect(StrEnum):
    REMOVE_FREE = "remove_free"
    REMOVE_AND_CANCEL = "remove_and_cancel"


def slot_start(slot_date: date, slot_time: time) -> datetime:
    return datetime.combine(slot_date, slot_time, tzinfo=MSK)


def _msk(now: datetime) -> datetime:
    return now.astimezone(MSK)


def ensure_bookable(slot: SlotState, *, active_in_month: int, now: datetime) -> None:
    """Можно ли записаться. Лимит — активные брони в месяце даты слота (вкл. прошедшие)."""
    if slot.removed or slot.booked or slot_start(slot.slot_date, slot.slot_time) <= now:
        raise DomainError("slot_unavailable", "Слот недоступен для записи")
    if active_in_month >= MONTHLY_LIMIT:
        raise DomainError(
            "monthly_limit", f"В этом месяце уже {MONTHLY_LIMIT} записи — больше нельзя"
        )


def cancel_effect(slot_date: date, slot_time: time, *, now: datetime) -> CancelEffect:
    """Что происходит со слотом при отмене пользователем (Д-2: только до начала)."""
    if slot_start(slot_date, slot_time) <= now:
        raise DomainError("too_late_to_cancel", "Запись уже началась — отменить нельзя")
    local = _msk(now)
    if slot_date == local.date() and local.time() >= LOCK_TIME:
        return CancelEffect.REMOVE_SLOT
    return CancelEffect.FREE_SLOT


def validate_new_slots(
    slot_date: date, times: Iterable[time], *, now: datetime
) -> tuple[time, ...]:
    """Д-8: дата не раньше сегодня. Слот на сегодня после 14:00 разрешён (как в боте)."""
    if slot_date < _msk(now).date():
        raise DomainError("slot_date_in_past", "Нельзя добавить слоты на прошедшую дату")
    return tuple(sorted(set(times)))


def delete_effect(slot: SlotState, *, now: datetime) -> DeleteEffect:
    """Д-9: занятый и уже начавшийся слот не удаляется — встреча состоялась."""
    if not slot.booked:
        return DeleteEffect.REMOVE_FREE
    if slot_start(slot.slot_date, slot.slot_time) <= now:
        raise DomainError("slot_in_past", "Занятый слот уже начался — удалить нельзя")
    return DeleteEffect.REMOVE_AND_CANCEL


def cleanup_date(now: datetime) -> date | None:
    """Дата, чьи свободные слоты пора удалить; None — до 14:00 МСК чистить нечего."""
    local = _msk(now)
    return local.date() if local.time() >= LOCK_TIME else None


def month_bounds(year: int, month: int) -> tuple[date, date]:
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, 1), date(year, month, last_day)


def overview_range(year: int, month: int) -> tuple[date, date]:
    """Обзор специалиста: месяц ± 3 дня соседних (как в боте)."""
    first, last = month_bounds(year, month)
    return first - OVERVIEW_MARGIN, last + OVERVIEW_MARGIN


def is_upcoming(slot_date: date, slot_time: time, *, now: datetime) -> bool:
    """«Мои записи»: начало слота ≥ now."""
    return slot_start(slot_date, slot_time) >= now


def _date_time(slot_date: date, slot_time: time) -> tuple[str, str]:
    return slot_date.strftime("%d.%m.%Y"), slot_time.strftime("%H:%M")


def text_booked(name: str, slot_date: date, slot_time: time, kind: Kind) -> str:
    d, t = _date_time(slot_date, slot_time)
    return f"✅ {name}\nЗаписался на {d} в {t}\n{KIND_TITLES[kind]}"


def text_cancelled_by_user(name: str, slot_date: date, slot_time: time, kind: Kind) -> str:
    d, t = _date_time(slot_date, slot_time)
    return f"❌ {name}\nОтменил запись на {d} в {t}\n{KIND_TITLES[kind]}"


def text_slot_removed(name: str, slot_date: date, slot_time: time) -> str:
    d, t = _date_time(slot_date, slot_time)
    return f"Слот {d} — {t} был удалён, запись {name} отменена"


def text_cleanup(times: Iterable[time]) -> str:
    listed = ", ".join(t.strftime("%H:%M") for t in sorted(times))
    return f"Свободные окна на сегодня {listed} были удалены"
