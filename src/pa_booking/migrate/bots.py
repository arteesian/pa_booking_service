"""Перенос будущих слотов, броней, каталога и выдач из MySQL ботов (спека §7).

Пользователь брони/выдачи — HUID бота как есть (Р-8): сопоставлять с сотрудниками
ЛК не нужно, брони людей не из КЦ тоже переносятся.

Чистое планирование (разбор строк ботов, отбор будущих, тип брони) отделено
от записи в Postgres (``apply_*``) — первое тестируется без БД. Чтение MySQL — в
``scripts/import_from_bots.py``.

Боты хранили дату и время строками ``DD.MM.YYYY`` / ``HH:MM`` (наследие, которое
не переносим, — CLAUDE.md); в Postgres они ложатся типами ``date`` / ``time``.
"""

from __future__ import annotations

import re
import uuid
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, time

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from pa_booking.db.appointments import add_slots
from pa_booking.db.directory import names_by_huid
from pa_booking.db.models import AppointmentBooking, AppointmentSlot, LibraryBook, LibraryLoan
from pa_booking.domain.appointments import BookingStatus, Kind
from pa_booking.domain.identity import Channel

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
class BookedSlot:
    """Будущая бронь бота: пользователь — HUID как есть (спека Р-8)."""

    slot_date: date
    slot_time: time
    huid: uuid.UUID
    comment: str | None
    kind: Kind | None  # None — тип не понятен, нужен --kind
    # Откуда тип (или почему не понятен) — для печати плана.
    source: str = ""


@dataclass(frozen=True)
class TelegramSlot:
    """Бронь времён Telegram (только ``tg_id``): не переносится, печатается в отчёт.

    Занятой её не сделать — нет HUID; свободной — специалист решит, что окно пустое.
    """

    slot_date: date
    slot_time: time
    tg_id: int


@dataclass(frozen=True)
class SlotPlan:
    free: tuple[tuple[date, time], ...]
    booked: tuple[BookedSlot, ...]
    telegram: tuple[TelegramSlot, ...] = ()

    @property
    def unresolved(self) -> tuple[BookedSlot, ...]:
        return tuple(b for b in self.booked if b.kind is None)


@dataclass(frozen=True)
class SlotsResult:
    added: int
    skipped: int  # уже были (повторный прогон)
    bookings: int


@dataclass(frozen=True)
class BookRow:
    """Строка ``Library_books``: книга и, если на руках, её выдача."""

    genre: str
    author: str
    title: str
    description: str
    huid: str | None = None
    tg_id: int | None = None
    start: date | None = None
    end: date | None = None


@dataclass(frozen=True)
class BookLoan:
    huid: uuid.UUID
    starts_on: date
    due_on: date


@dataclass(frozen=True)
class PlannedBook:
    genre: str
    author: str
    title: str
    description: str
    loan: BookLoan | None


@dataclass(frozen=True)
class BooksPlan:
    books: tuple[PlannedBook, ...]
    # Книги на руках у Telegram-пользователей (без HUID): переносятся свободными,
    # сверяет библиотекарь.
    telegram: tuple[str, ...] = ()

    @property
    def loans(self) -> int:
        return sum(b.loan is not None for b in self.books)


@dataclass(frozen=True)
class BooksResult:
    books: int
    loans: int


def parse_slot(date_str: str, time_str: str) -> tuple[date, time]:
    """``05.10.2026`` + ``09:30`` → (date, time). Иной формат — ``ValueError``."""
    if not _DATE_RE.match(date_str) or not _TIME_RE.match(time_str):
        raise ValueError(f"неожиданный формат даты/времени: {date_str!r} {time_str!r}")
    return (
        datetime.strptime(date_str, "%d.%m.%Y").date(),
        datetime.strptime(time_str, "%H:%M").time(),
    )


def parse_huid(raw: str) -> uuid.UUID:
    try:
        return uuid.UUID(raw.strip())
    except ValueError as exc:
        raise ValueError(f"не HUID: {raw!r}") from exc


def parse_kind(value: str) -> tuple[uuid.UUID, Kind]:
    """``<HUID>=<psy|mkr>`` → (HUID, тип)."""
    try:
        huid, kind = value.split("=", 1)
        return parse_huid(huid), Kind(kind.strip())
    except ValueError as exc:
        raise ValueError(f"--kind {value!r}: ожидается <HUID>=<psy|mkr>") from exc


def kind_from_comment(comment: str | None) -> Kind | None:
    """Тип брони из свободного ответа бота — только если он однозначен.

    Бот спрашивал «психолог или МКР?» и сохранял ответ как есть (Д-1). Берём тип,
    только если в ответе ровно одно из «псих» / «мкр»; иначе — None, решает человек.
    """
    text = (comment or "").lower()
    psy, mkr = "псих" in text, "мкр" in text
    if psy == mkr:
        return None
    return Kind.PSY if psy else Kind.MKR


def _kind(
    huid: uuid.UUID, comment: str | None, kinds: Mapping[uuid.UUID, Kind]
) -> tuple[Kind | None, str]:
    if huid in kinds:
        return kinds[huid], "тип из --kind"
    kind = kind_from_comment(comment)
    if kind is None:
        return None, "тип не понятен из комментария — нужен --kind"
    return kind, "тип из комментария"


def plan_slots(
    rows: Iterable[SlotRow],
    *,
    today: date,
    kinds: Mapping[uuid.UUID, Kind] | None = None,
) -> SlotPlan:
    """Будущие (с ``today`` включительно) слоты: свободные, занятые и Telegram.

    Занятым считается слот с HUID **или** ``tg_id`` — как в боте. Бронь с HUID
    переносится на этот HUID; тип — из ``--kind`` или однозначного комментария.
    ``--kind`` на HUID без будущей брони — ошибка: опечатка не должна молча оставить
    бронь без типа.
    """
    explicit = kinds or {}
    free: list[tuple[date, time]] = []
    booked: list[BookedSlot] = []
    telegram: list[TelegramSlot] = []
    for number, row in enumerate(rows, start=1):
        try:
            slot_date, slot_time = parse_slot(row.date_str, row.time_str)
            huid = None if row.huid is None else parse_huid(row.huid)
        except ValueError as exc:
            raise ValueError(f"строка {number}: {exc}") from exc
        if slot_date < today:
            continue
        if huid is not None:
            kind, source = _kind(huid, row.comment, explicit)
            booked.append(BookedSlot(slot_date, slot_time, huid, row.comment, kind, source))
        elif row.tg_id is not None:
            telegram.append(TelegramSlot(slot_date, slot_time, row.tg_id))
        else:
            free.append((slot_date, slot_time))
    unknown = sorted(str(h) for h in set(explicit) - {b.huid for b in booked})
    if unknown:
        raise ValueError(f"--kind: HUID не найден среди будущих броней: {', '.join(unknown)}")

    def at(x: BookedSlot | TelegramSlot) -> tuple[date, time]:
        return x.slot_date, x.slot_time

    return SlotPlan(
        tuple(sorted(free)), tuple(sorted(booked, key=at)), tuple(sorted(telegram, key=at))
    )


def apply_slots(session: Session, plan: SlotPlan, *, now: datetime) -> SlotsResult:
    """Записать план. Повторный прогон безопасен: существующие слоты пропускаются,
    бронь на уже занятый слот второй раз не создаётся. Коммит — у вызывающего.

    Бронь — от канала ``express`` (она из бота); ``user_name`` — ФИО из ростера, если
    человек там есть, иначе пусто (имя тогда подставит ростер или HUID, §3.1).
    """
    if plan.unresolved:
        raise ValueError(f"{len(plan.unresolved)} броней без типа — укажите --kind")
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

    names = names_by_huid(session, {b.huid for b in plan.booked})
    bookings = 0
    for b in plan.booked:
        assert b.kind is not None  # проверено выше
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
                user_huid=b.huid,
                user_name=names.get(b.huid),
                channel=Channel.EXPRESS,
                kind=b.kind,
                status=BookingStatus.ACTIVE,
                created_at=now,
            )
        )
        bookings += 1
    session.flush()
    return SlotsResult(added=added, skipped=skipped, bookings=bookings)


def plan_books(rows: Iterable[BookRow]) -> BooksPlan:
    """Каталог с выдачами. Пустое поле книги, кривой HUID или выдача без дат — ``ValueError``.

    Книга с HUID — на руках: выдача переносится как есть, просроченная тоже (Д-3).
    Только ``tg_id`` — книга свободна, название — в отчёт.
    """
    books: list[PlannedBook] = []
    telegram: list[str] = []
    for row in rows:
        fields = {
            "genre": row.genre.strip(),
            "author": row.author.strip(),
            "title": row.title.strip(),
            "description": row.description.strip(),
        }
        empty = [name for name, value in fields.items() if not value]
        if empty:
            raise ValueError(f"книга {row.title!r}: пустые поля {', '.join(empty)}")
        loan = None
        if row.huid is not None:
            if row.start is None or row.end is None:
                raise ValueError(f"книга {row.title!r}: на руках у {row.huid}, но нет дат брони")
            loan = BookLoan(parse_huid(row.huid), row.start, row.end)
        elif row.tg_id is not None:
            telegram.append(fields["title"])
        books.append(PlannedBook(**fields, loan=loan))
    return BooksPlan(tuple(books), tuple(telegram))


def apply_books(
    session: Session, plan: BooksPlan, *, now: datetime, replace: bool = False
) -> BooksResult:
    """Перенести каталог и выдачи. Коммит — у вызывающего.

    Без ``replace`` — однократно: непустой каталог — отказ. С ``replace`` (окно
    переключения, спека §7) каталог переливается заново — но только пока в сервисе нет
    ни одной выдачи: id книг меняются, выдачи потеряли бы свои книги.
    """
    if replace:
        if session.scalar(select(func.count()).select_from(LibraryLoan)):
            raise ValueError("в library_loans есть выдачи — перезаливка каталога их потеряет")
        session.execute(delete(LibraryBook))
    elif session.scalar(select(func.count()).select_from(LibraryBook)):
        raise ValueError("каталог library_books не пуст — перенос книг уже был (см. --replace)")

    names = names_by_huid(session, {b.loan.huid for b in plan.books if b.loan})
    for b in plan.books:
        book = LibraryBook(
            genre=b.genre, author=b.author, title=b.title, description=b.description, created_at=now
        )
        session.add(book)
        if b.loan is not None:
            session.flush()  # нужен book.id
            session.add(
                LibraryLoan(
                    book_id=book.id,
                    user_huid=b.loan.huid,
                    user_name=names.get(b.loan.huid),
                    channel=Channel.EXPRESS,
                    starts_on=b.loan.starts_on,
                    due_on=b.loan.due_on,
                    created_at=now,
                )
            )
    session.flush()
    return BooksResult(books=len(plan.books), loans=plan.loans)
