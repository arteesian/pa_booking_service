"""Ручки записей к психологу и на консультацию МКР (спека §5.1).

Порядок в каждой меняющей ручке: блокировки → правило домена → изменение →
``commit`` → уведомление фоновой задачей. Уведомление уходит только после коммита
и операцию не откатывает (CLAUDE.md).
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterable
from datetime import date
from itertools import groupby
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Response, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from pa_booking.api.appointments_schemas import (
    BookingCreate,
    BookingOut,
    DaySlotsOut,
    OverviewBookingOut,
    OverviewDayOut,
    OverviewSlotOut,
    SlotOut,
    SlotsAddedOut,
    SlotsCreate,
)
from pa_booking.api.deps import DbSession, Now, Principal, PsychologistPrincipal
from pa_booking.core.config import Settings, get_settings
from pa_booking.core.errors import ApiError
from pa_booking.db import appointments as repo
from pa_booking.db.directory import display_names
from pa_booking.db.models import AppointmentBooking, AppointmentSlot
from pa_booking.domain.appointments import (
    KIND_TITLES,
    MSK,
    STATUS_TITLES,
    BookingStatus,
    CancelEffect,
    DeleteEffect,
    cancel_effect,
    delete_effect,
    ensure_bookable,
    is_upcoming,
    month_bounds,
    overview_range,
    slot_start,
    text_booked,
    text_cancelled_by_user,
    text_slot_removed,
    validate_new_slots,
)
from pa_booking.export.xlsx import build_xlsx
from pa_booking.notify.botx import Module, Notifier, make_notifier, notify_safely

MODULE: Module = "appointments"
MONTH_PATTERN = r"^\d{4}-(0[1-9]|1[0-2])$"
XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
EXPORT_HEADERS = ["Дата", "Время", "ФИО", "Тип", "Статус"]

router = APIRouter(prefix="/appointments", tags=["appointments"])


def get_appointments_notifier(
    settings: Annotated[Settings, Depends(get_settings)],
) -> Notifier | None:
    """Notifier модуля записей; None — BotX не настроен (сбой посчитает notify_safely)."""
    return make_notifier(settings, MODULE)


AppointmentsNotifier = Annotated[Notifier | None, Depends(get_appointments_notifier)]


def _by_date[T](items: Iterable[T], key: Callable[[T], date]) -> list[tuple[date, list[T]]]:
    """Сгруппировать уже отсортированные по дате элементы."""
    return [(d, list(group)) for d, group in groupby(items, key=key)]


def _name(session: Session, employee_id: uuid.UUID) -> str:
    return display_names(session, [employee_id])[employee_id]


def _booking_out(booking: AppointmentBooking, slot: AppointmentSlot) -> BookingOut:
    return BookingOut(
        id=booking.id,
        slot_id=slot.id,
        date=slot.slot_date,
        time=slot.slot_time,
        kind=booking.kind,
        status=booking.status,
    )


def _parse_month(month: str) -> tuple[int, int]:
    year, mon = month.split("-")
    return int(year), int(mon)


@router.get("/slots", response_model=list[DaySlotsOut])
def list_free_slots(
    session: DbSession,
    _principal: Principal,
    now: Now,
    date_from: Annotated[date, Query(alias="from")],
    date_to: Annotated[date, Query(alias="to")],
) -> list[DaySlotsOut]:
    """Свободные слоты: неудалённые, без активной брони, начало > now."""
    slots = [
        s
        for s in repo.free_slots(session, date_from, date_to)
        if slot_start(s.slot_date, s.slot_time) > now
    ]
    return [
        DaySlotsOut(date=d, slots=[SlotOut(id=s.id, time=s.slot_time) for s in group])
        for d, group in _by_date(slots, lambda s: s.slot_date)
    ]


@router.post("/bookings", status_code=status.HTTP_201_CREATED, response_model=BookingOut)
def create_booking(
    body: BookingCreate,
    session: DbSession,
    principal: Principal,
    now: Now,
    notifier: AppointmentsNotifier,
    background: BackgroundTasks,
) -> BookingOut:
    # Сначала сотрудник, потом слот: этот порядок блокировок — единственный, где
    # берутся обе, поэтому взаимоблокировки нет.
    repo.lock_employee(session, principal.employee_id)
    locked = repo.lock_slot(session, body.slot_id)
    if locked is None:
        raise ApiError(404, "not_found", "Слот не найден")
    slot = locked.slot
    active = repo.count_active_in_month(
        session, principal.employee_id, slot.slot_date.year, slot.slot_date.month
    )
    ensure_bookable(locked.state(), active_in_month=active, now=now)

    booking = AppointmentBooking(
        slot_id=slot.id,
        employee_id=principal.employee_id,
        kind=body.kind,
        status=BookingStatus.ACTIVE,
        created_at=now,
    )
    session.add(booking)
    try:
        session.commit()
    except IntegrityError as exc:
        # Страховка: блокировка слота уже не даёт сюда дойти, но индекс — последнее слово.
        session.rollback()
        raise ApiError(409, "slot_unavailable", "Слот недоступен для записи") from exc

    text = text_booked(
        _name(session, principal.employee_id), slot.slot_date, slot.slot_time, body.kind
    )
    background.add_task(notify_safely, notifier, text, module=MODULE)
    return _booking_out(booking, slot)


@router.get("/bookings/my", response_model=list[BookingOut])
def my_bookings(session: DbSession, principal: Principal, now: Now) -> list[BookingOut]:
    """Будущие записи (начало ≥ now): активные и отменённые специалистом."""
    rows = repo.my_bookings(
        session,
        principal.employee_id,
        [BookingStatus.ACTIVE, BookingStatus.CANCELLED_BY_SPECIALIST],
        since=now.astimezone(MSK).date(),
    )
    return [
        _booking_out(r.booking, r.slot)
        for r in rows
        if is_upcoming(r.slot.slot_date, r.slot.slot_time, now=now)
    ]


@router.post("/bookings/{booking_id}/cancel", response_model=BookingOut)
def cancel_booking(
    booking_id: int,
    session: DbSession,
    principal: Principal,
    now: Now,
    notifier: AppointmentsNotifier,
    background: BackgroundTasks,
) -> BookingOut:
    not_found = ApiError(404, "not_found", "Запись не найдена")
    found = repo.find_booking(session, booking_id)
    # Чужая бронь — 404, а не 403: существование чужого не раскрываем.
    if found is None or found.booking.employee_id != principal.employee_id:
        raise not_found
    locked = repo.lock_slot(session, found.slot.id)
    # Под блокировкой бронь могла уже смениться (отменена или слот удалён специалистом).
    if locked is None or locked.booking is None or locked.booking.id != booking_id:
        raise not_found
    booking, slot = locked.booking, locked.slot

    effect = cancel_effect(slot.slot_date, slot.slot_time, now=now)
    booking.status = BookingStatus.CANCELLED_BY_USER
    booking.cancelled_at = now
    if effect is CancelEffect.REMOVE_SLOT:
        slot.removed_at = now
    session.commit()

    text = text_cancelled_by_user(
        _name(session, principal.employee_id), slot.slot_date, slot.slot_time, booking.kind
    )
    background.add_task(notify_safely, notifier, text, module=MODULE)
    return _booking_out(booking, slot)


@router.get("/admin/slots", response_model=list[OverviewDayOut])
def admin_overview(
    session: DbSession,
    _principal: PsychologistPrincipal,
    month: Annotated[str, Query(pattern=MONTH_PATTERN)],
) -> list[OverviewDayOut]:
    """Обзор месяца ± 3 дня: все неудалённые слоты, у занятых — ФИО и тип."""
    rows = repo.overview(session, *overview_range(*_parse_month(month)))
    names = display_names(session, {r.booking.employee_id for r in rows if r.booking})

    def slot_out(r: repo.SlotWithBooking) -> OverviewSlotOut:
        b = r.booking
        return OverviewSlotOut(
            id=r.slot.id,
            time=r.slot.slot_time,
            booking=None
            if b is None
            else OverviewBookingOut(
                id=b.id,
                employee_id=b.employee_id,
                full_name=names[b.employee_id],
                kind=b.kind,
            ),
        )

    return [
        OverviewDayOut(date=d, slots=[slot_out(r) for r in group])
        for d, group in _by_date(rows, lambda r: r.slot.slot_date)
    ]


@router.post("/admin/slots", response_model=SlotsAddedOut)
def admin_add_slots(
    body: SlotsCreate, session: DbSession, _principal: PsychologistPrincipal, now: Now
) -> SlotsAddedOut:
    times = validate_new_slots(body.date, body.times, now=now)
    added, duplicates = repo.add_slots(session, body.date, times, now=now)
    session.commit()
    return SlotsAddedOut(added=added, duplicates=duplicates)


@router.delete("/admin/slots/{slot_id}", status_code=status.HTTP_204_NO_CONTENT)
def admin_delete_slot(
    slot_id: int,
    session: DbSession,
    _principal: PsychologistPrincipal,
    now: Now,
    notifier: AppointmentsNotifier,
    background: BackgroundTasks,
) -> Response:
    locked = repo.lock_slot(session, slot_id)
    if locked is None or locked.slot.removed_at is not None:
        raise ApiError(404, "not_found", "Слот не найден")
    slot, booking = locked.slot, locked.booking

    effect = delete_effect(locked.state(), now=now)
    slot.removed_at = now
    if effect is DeleteEffect.REMOVE_AND_CANCEL and booking is not None:
        booking.status = BookingStatus.CANCELLED_BY_SPECIALIST
        booking.cancelled_at = now
    session.commit()

    if booking is not None:
        text = text_slot_removed(
            _name(session, booking.employee_id), slot.slot_date, slot.slot_time
        )
        background.add_task(notify_safely, notifier, text, module=MODULE)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/admin/export", response_class=Response)
def admin_export(
    session: DbSession,
    _principal: PsychologistPrincipal,
    month: Annotated[str | None, Query(pattern=MONTH_PATTERN)] = None,
    all_time: Annotated[bool, Query(alias="all")] = False,
) -> Response:
    """xlsx за месяц (``?month=YYYY-MM``) или за всё время (``?all=true``).

    Строки — как в боте плюс статус: брони в любом статусе и свободные
    неудалённые слоты (см. ``repo.export_rows``).
    """
    if all_time == (month is not None):
        raise ApiError(422, "validation_error", "Укажите либо month=YYYY-MM, либо all=true")
    if month is not None:
        rows = repo.export_rows(session, *month_bounds(*_parse_month(month)))
        suffix, title = month, f"Записи {month}"
    else:
        rows = repo.export_rows(session, None, None)
        suffix, title = "all", "Все записи"

    names = display_names(session, {r.employee_id for r in rows if r.employee_id})
    data = build_xlsx(
        title,
        EXPORT_HEADERS,
        (
            (
                r.slot_date,
                r.slot_time,
                None if r.employee_id is None else names[r.employee_id],
                None if r.kind is None else KIND_TITLES[r.kind],
                None if r.status is None else STATUS_TITLES[r.status],
            )
            for r in rows
        ),
    )
    return Response(
        content=data,
        media_type=XLSX_MEDIA_TYPE,
        headers={"Content-Disposition": f"attachment; filename=appointments_{suffix}.xlsx"},
    )
