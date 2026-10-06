"""Перенос будущих слотов и каталога книг из MySQL ботов (спека §7).

Чистое планирование (разбор строк ботов, отбор будущих, сопоставление брони) отделено
от записи в Postgres (``apply_*``) — первое тестируется без БД. Чтение MySQL — в
``scripts/import_from_bots.py``.

Боты хранили дату и время строками ``DD.MM.YYYY`` / ``HH:MM`` (наследие, которое
не переносим, — CLAUDE.md); в Postgres они ложатся типами ``date`` / ``time``.
"""

from __future__ import annotations

import re
import uuid
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from pa_booking.db.appointments import add_slots
from pa_booking.db.models import AppointmentBooking, AppointmentSlot, LibraryBook
from pa_booking.domain.appointments import BookingStatus, Kind

_DATE_RE = re.compile(r"^\d{2}\.\d{2}\.\d{4}$")
_TIME_RE = re.compile(r"^\d{2}:\d{2}$")


@dataclass(frozen=True)
class SlotRow:
    """Строка ``record_psy_pari`` как есть."""

    date_str: str
    time_str: str
    huid: str | None
    tg_id: int | None
    comment: str | None


@dataclass(frozen=True)
class Assignment:
    """Ручное сопоставление брони бота: HUID eXpress → сотрудник и тип записи."""

    huid: str
    employee_id: uuid.UUID
    kind: Kind


@dataclass(frozen=True)
class BookedSlot:
    slot_date: date
    slot_time: time
    huid: str | None
    tg_id: int | None
    comment: str | None
    assignment: Assignment | None


@dataclass(frozen=True)
class SlotPlan:
    free: tuple[tuple[date, time], ...]
    booked: tuple[BookedSlot, ...]

    @property
    def unassigned(self) -> tuple[BookedSlot, ...]:
        return tuple(b for b in self.booked if b.assignment is None)


@dataclass(frozen=True)
class SlotsResult:
    added: int
    skipped: int  # уже были (повторный прогон)
    bookings: int


@dataclass(frozen=True)
class BookRow:
    genre: str
    author: str
    title: str
    description: str


def parse_slot(date_str: str, time_str: str) -> tuple[date, time]:
    """``05.10.2026`` + ``09:30`` → (date, time). Иной формат — ``ValueError``."""
    if not _DATE_RE.match(date_str) or not _TIME_RE.match(time_str):
        raise ValueError(f"неожиданный формат даты/времени: {date_str!r} {time_str!r}")
    return (
        datetime.strptime(date_str, "%d.%m.%Y").date(),
        datetime.strptime(time_str, "%H:%M").time(),
    )


def parse_assign(value: str) -> Assignment:
    """``<HUID>=<employee_id>:<psy|mkr>`` → :class:`Assignment`."""
    try:
        huid, rest = value.split("=", 1)
        employee, kind = rest.rsplit(":", 1)
        return Assignment(huid.strip(), uuid.UUID(employee.strip()), Kind(kind.strip()))
    except ValueError as exc:
        raise ValueError(f"--assign {value!r}: ожидается <HUID>=<employee_id>:<psy|mkr>") from exc


def plan_slots(
    rows: Iterable[SlotRow], *, today: date, assignments: Sequence[Assignment]
) -> SlotPlan:
    """Будущие (с ``today`` включительно) слоты: свободные и занятые.

    Занятым считается слот с HUID **или** ``tg_id`` (бронь времён Telegram) — как в
    боте. Сопоставление — по HUID; ``--assign`` на HUID без будущей брони — ошибка:
    опечатка не должна молча оставить бронь без переноса.
    """
    by_huid = {a.huid: a for a in assignments}
    free: list[tuple[date, time]] = []
    booked: list[BookedSlot] = []
    for number, row in enumerate(rows, start=1):
        try:
            slot_date, slot_time = parse_slot(row.date_str, row.time_str)
        except ValueError as exc:
            raise ValueError(f"строка {number}: {exc}") from exc
        if slot_date < today:
            continue
        if row.huid is None and row.tg_id is None:
            free.append((slot_date, slot_time))
            continue
        assignment = by_huid.get(row.huid) if row.huid is not None else None
        booked.append(
            BookedSlot(slot_date, slot_time, row.huid, row.tg_id, row.comment, assignment)
        )
    used = {b.assignment.huid for b in booked if b.assignment is not None}
    unknown = sorted(set(by_huid) - used)
    if unknown:
        raise ValueError(f"--assign: HUID не найден среди будущих броней: {', '.join(unknown)}")
    return SlotPlan(
        tuple(sorted(free)), tuple(sorted(booked, key=lambda b: (b.slot_date, b.slot_time)))
    )


def apply_slots(session: Session, plan: SlotPlan, *, now: datetime) -> SlotsResult:
    """Записать план. Повторный прогон безопасен: существующие слоты пропускаются,
    бронь на уже занятый слот второй раз не создаётся. Коммит — у вызывающего."""
    if plan.unassigned:
        raise ValueError(f"{len(plan.unassigned)} занятых слотов без --assign — перенос остановлен")
    by_date: dict[date, list[time]] = defaultdict(list)
    for d, t in plan.free:
        by_date[d].append(t)
    for b in plan.booked:
        by_date[b.slot_date].append(b.slot_time)

    added = skipped = 0
    for d, times in sorted(by_date.items()):
        new, duplicates = add_slots(session, d, times, now=now)
        added += len(new)
        skipped += len(duplicates)

    bookings = 0
    for b in plan.booked:
        assert b.assignment is not None  # проверено выше
        slot_id = session.scalar(
            select(AppointmentSlot.id).where(
                AppointmentSlot.slot_date == b.slot_date,
                AppointmentSlot.slot_time == b.slot_time,
                AppointmentSlot.removed_at.is_(None),
            )
        )
        taken = session.scalar(
            select(func.count())
            .select_from(AppointmentBooking)
            .where(
                AppointmentBooking.slot_id == slot_id,
                AppointmentBooking.status == BookingStatus.ACTIVE,
            )
        )
        if taken:
            continue
        session.add(
            AppointmentBooking(
                slot_id=slot_id,
                employee_id=b.assignment.employee_id,
                kind=b.assignment.kind,
                status=BookingStatus.ACTIVE,
                created_at=now,
            )
        )
        bookings += 1
    session.flush()
    return SlotsResult(added=added, skipped=skipped, bookings=bookings)


def clean_book(row: BookRow) -> BookRow:
    """Обрезать пробелы; пустое поле — ``ValueError`` (в Postgres поля NOT NULL)."""
    cleaned = BookRow(
        row.genre.strip(), row.author.strip(), row.title.strip(), row.description.strip()
    )
    empty = [name for name, value in vars(cleaned).items() if not value]
    if empty:
        raise ValueError(f"книга {row.title!r}: пустые поля {', '.join(empty)}")
    return cleaned


def apply_books(session: Session, rows: Iterable[BookRow], *, now: datetime) -> int:
    """Перенести каталог свободными книгами. Однократно: непустой каталог — отказ."""
    if session.scalar(select(func.count()).select_from(LibraryBook)):
        raise ValueError("каталог library_books не пуст — перенос книг уже был")
    books = [clean_book(r) for r in rows]
    session.add_all(
        LibraryBook(
            genre=b.genre,
            author=b.author,
            title=b.title,
            description=b.description,
            created_at=now,
        )
        for b in books
    )
    session.flush()
    return len(books)
