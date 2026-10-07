"""Персистентность записей: слоты и брони.

Правила — в ``domain.appointments``; здесь только чтение/запись и блокировки.
Коммит — у вызывающего: одна операция API = одна транзакция.

Гонки (CLAUDE.md: «целостность — в БД»):

- всё, что меняет слот или его бронь, сначала берёт строку слота ``FOR UPDATE``
  (:func:`lock_slot`) — запись, отмена, удаление и чистка идут по очереди;
- лимит «4 в месяц» индексом не выразить, поэтому запись берёт транзакционную
  advisory-блокировку по пользователю (:func:`lock_user`). Ключ — HUID: один
  человек из ЛК и из бота — одна блокировка и один лимит (спека Р-8).
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, time

from sqlalchemy import Exists, Select, and_, exists, func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session
from sqlalchemy.sql import ColumnElement

from pa_booking.db.models import AppointmentBooking, AppointmentSlot
from pa_booking.domain.appointments import BookingStatus, Kind, SlotState, month_bounds


@dataclass(frozen=True)
class SlotWithBooking:
    slot: AppointmentSlot
    booking: AppointmentBooking | None  # только активная бронь

    def state(self) -> SlotState:
        return SlotState(
            slot_date=self.slot.slot_date,
            slot_time=self.slot.slot_time,
            removed=self.slot.removed_at is not None,
            booked=self.booking is not None,
        )


@dataclass(frozen=True)
class BookingWithSlot:
    booking: AppointmentBooking
    slot: AppointmentSlot


@dataclass(frozen=True)
class ExportRow:
    """Строка выгрузки; у свободного слота пользователь, тип и статус пустые."""

    slot_date: date
    slot_time: time
    user_huid: uuid.UUID | None
    user_name: str | None
    kind: Kind | None
    status: BookingStatus | None


def _active_booking_of(slot_id: int) -> Select[tuple[AppointmentBooking]]:
    return select(AppointmentBooking).where(
        AppointmentBooking.slot_id == slot_id,
        AppointmentBooking.status == BookingStatus.ACTIVE,
    )


def _has_active_booking() -> Exists:
    return exists().where(
        AppointmentBooking.slot_id == AppointmentSlot.id,
        AppointmentBooking.status == BookingStatus.ACTIVE,
    )


def lock_slot(session: Session, slot_id: int) -> SlotWithBooking | None:
    """Слот под ``FOR UPDATE`` и его активная бронь. None — слота нет.

    ``populate_existing``: если слот или бронь уже загружены в сессию до блокировки
    (отмена сначала ищет бронь), SQLAlchemy иначе вернёт объект из identity map со
    старыми полями — решение принималось бы по данным до чужого коммита.
    """
    slot = session.scalars(
        select(AppointmentSlot)
        .where(AppointmentSlot.id == slot_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one_or_none()
    if slot is None:
        return None
    booking = session.scalars(
        _active_booking_of(slot.id).execution_options(populate_existing=True)
    ).one_or_none()
    return SlotWithBooking(slot, booking)


def lock_user(session: Session, huid: uuid.UUID) -> None:
    """Сериализовать записи одного пользователя до конца транзакции."""
    session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": f"appointments:{huid}"},
    )


def find_booking(session: Session, booking_id: int) -> BookingWithSlot | None:
    row = session.execute(
        select(AppointmentBooking, AppointmentSlot)
        .join(AppointmentSlot, AppointmentSlot.id == AppointmentBooking.slot_id)
        .where(AppointmentBooking.id == booking_id)
    ).one_or_none()
    return None if row is None else BookingWithSlot(row[0], row[1])


def count_active_in_month(session: Session, huid: uuid.UUID, year: int, month: int) -> int:
    """Активные брони пользователя со ``slot_date`` в месяце — прошедшие тоже (как в боте),
    из обоих каналов."""
    first, last = month_bounds(year, month)
    return (
        session.scalar(
            select(func.count())
            .select_from(AppointmentBooking)
            .join(AppointmentSlot, AppointmentSlot.id == AppointmentBooking.slot_id)
            .where(
                AppointmentBooking.user_huid == huid,
                AppointmentBooking.status == BookingStatus.ACTIVE,
                AppointmentSlot.slot_date.between(first, last),
            )
        )
        or 0
    )


def free_slots(session: Session, date_from: date, date_to: date) -> list[AppointmentSlot]:
    """Неудалённые слоты без активной брони. «Начало > now» отсекает вызывающий."""
    return list(
        session.scalars(
            select(AppointmentSlot)
            .where(
                AppointmentSlot.slot_date.between(date_from, date_to),
                AppointmentSlot.removed_at.is_(None),
                ~_has_active_booking(),
            )
            .order_by(AppointmentSlot.slot_date, AppointmentSlot.slot_time)
        )
    )


def add_slots(
    session: Session, slot_date: date, times: Iterable[time], *, now: datetime
) -> tuple[list[time], list[time]]:
    """Вставить слоты, пропуская совпавшие с неудалённым. → (``added``, ``duplicates``)."""
    wanted = sorted(set(times))
    if not wanted:
        return [], []
    stmt = (
        insert(AppointmentSlot)
        .values([{"slot_date": slot_date, "slot_time": t, "created_at": now} for t in wanted])
        # Конфликт — по частичному индексу uq_appointment_slots_active: удалённый слот
        # с тем же временем дублем не считается.
        .on_conflict_do_nothing(
            index_elements=["slot_date", "slot_time"],
            index_where=AppointmentSlot.removed_at.is_(None),
        )
        .returning(AppointmentSlot.slot_time)
    )
    added = set(session.scalars(stmt))
    return sorted(added), [t for t in wanted if t not in added]


def my_bookings(
    session: Session,
    huid: uuid.UUID,
    statuses: Iterable[BookingStatus],
    *,
    since: date,
) -> list[BookingWithSlot]:
    """Брони пользователя в статусах ``statuses`` со ``slot_date`` ≥ ``since``."""
    rows = session.execute(
        select(AppointmentBooking, AppointmentSlot)
        .join(AppointmentSlot, AppointmentSlot.id == AppointmentBooking.slot_id)
        .where(
            AppointmentBooking.user_huid == huid,
            AppointmentBooking.status.in_(list(statuses)),
            AppointmentSlot.slot_date >= since,
        )
        .order_by(AppointmentSlot.slot_date, AppointmentSlot.slot_time, AppointmentBooking.id)
    ).all()
    return [BookingWithSlot(b, s) for b, s in rows]


def overview(session: Session, date_from: date, date_to: date) -> list[SlotWithBooking]:
    """Неудалённые слоты диапазона с активной бронью (если есть)."""
    rows = session.execute(
        select(AppointmentSlot, AppointmentBooking)
        .outerjoin(
            AppointmentBooking,
            and_(
                AppointmentBooking.slot_id == AppointmentSlot.id,
                AppointmentBooking.status == BookingStatus.ACTIVE,
            ),
        )
        .where(
            AppointmentSlot.slot_date.between(date_from, date_to),
            AppointmentSlot.removed_at.is_(None),
        )
        .order_by(AppointmentSlot.slot_date, AppointmentSlot.slot_time)
    ).all()
    return [SlotWithBooking(s, b) for s, b in rows]


def remove_free_slots_on(session: Session, day: date, *, now: datetime) -> list[time]:
    """Пометить удалёнными свободные слоты даты ``day``. → их время, по возрастанию.

    Слоты дня берутся ``FOR UPDATE``: запись, начатая параллельно, либо успеет
    (и слот уже занят), либо дождётся чистки и увидит слот удалённым.
    """
    slots = list(
        session.scalars(
            select(AppointmentSlot)
            .where(AppointmentSlot.slot_date == day, AppointmentSlot.removed_at.is_(None))
            .order_by(AppointmentSlot.slot_time)
            .with_for_update()
        )
    )
    if not slots:
        return []
    booked = set(
        session.scalars(
            select(AppointmentBooking.slot_id).where(
                AppointmentBooking.slot_id.in_([s.id for s in slots]),
                AppointmentBooking.status == BookingStatus.ACTIVE,
            )
        )
    )
    removed: list[time] = []
    for slot in slots:
        if slot.id not in booked:
            slot.removed_at = now
            removed.append(slot.slot_time)
    session.flush()
    return removed


def export_rows(session: Session, date_from: date | None, date_to: date | None) -> list[ExportRow]:
    """Строки выгрузки, как в боте плюс статусы (спека §4.1, решение по Task 2.6).

    - каждая бронь со ``slot_date`` в периоде — в любом статусе;
    - каждый неудалённый слот периода без активной брони — строкой без пользователя.

    Удалённые свободные слоты (чистка, специалист) не выгружаются: бот их стирал.
    ``None`` в границе — без ограничения с этой стороны.
    """
    period: list[ColumnElement[bool]] = []
    if date_from is not None:
        period.append(AppointmentSlot.slot_date >= date_from)
    if date_to is not None:
        period.append(AppointmentSlot.slot_date <= date_to)

    bookings = session.execute(
        select(
            AppointmentSlot.slot_date,
            AppointmentSlot.slot_time,
            AppointmentBooking.user_huid,
            AppointmentBooking.user_name,
            AppointmentBooking.kind,
            AppointmentBooking.status,
            AppointmentBooking.id,
        )
        .join(AppointmentSlot, AppointmentSlot.id == AppointmentBooking.slot_id)
        .where(*period)
    ).all()
    free = session.execute(
        select(AppointmentSlot.slot_date, AppointmentSlot.slot_time).where(
            *period, AppointmentSlot.removed_at.is_(None), ~_has_active_booking()
        )
    ).all()

    # Сортировка: дата, время; на одном слоте — сначала брони по порядку создания,
    # затем строка «свободен» (слот освободился после отмены).
    keyed: list[tuple[date, time, int, ExportRow]] = [
        (d, t, booking_id, ExportRow(d, t, huid, name, kind, status))
        for d, t, huid, name, kind, status, booking_id in bookings
    ]
    keyed += [(d, t, 2**63, ExportRow(d, t, None, None, None, None)) for d, t in free]
    keyed.sort(key=lambda row: row[:3])
    return [row[3] for row in keyed]
