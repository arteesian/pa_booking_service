"""Два канала над одной БД (спека §3.1, Р-8…Р-11, Д-10).

Один человек — один HUID: брони из ЛК и из бота видны в обоих каналах и считаются
в один лимит. Остальные правила записей и библиотеки — в ``test_api_*``.
"""

from __future__ import annotations

import uuid
from datetime import date, time

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from pa_booking.db.models import AppointmentBooking, AppointmentSlot, LibraryBook, LibraryLoan
from pa_booking.domain.appointments import Kind, text_booked
from pa_booking.domain.identity import Channel
from pa_booking.domain.library import text_loaned
from tests.integration.conftest import (
    BOT_KEYS,
    LIB_ADMIN,
    PSY_ADMIN,
    ApiEnv,
    add_roster,
    bot_headers,
    lk_headers,
)

pytestmark = pytest.mark.db

TODAY = date(2026, 10, 5)  # «сейчас» — 05.10.2026 10:00 МСК
HUID = uuid.UUID("11111111-1111-1111-1111-111111111111")


def add_slot(db: Session, d: date = TODAY, t: time = time(16, 0)) -> int:
    slot = AppointmentSlot(slot_date=d, slot_time=t)
    db.add(slot)
    db.commit()
    return slot.id


def add_book(db: Session) -> int:
    book = LibraryBook(genre="Роман", author="Булгаков", title="Мастер", description="—")
    db.add(book)
    db.commit()
    return book.id


def book_slot(api: ApiEnv, slot_id: int, h: dict[str, str]) -> tuple[int, dict[str, object]]:
    r = api.client.post(
        "/appointments/bookings", json={"slot_id": slot_id, "kind": "psy"}, headers=h
    )
    body: dict[str, object] = r.json()
    return r.status_code, body


# --- ЛК без привязки eXpress (Д-10) ---


@pytest.mark.parametrize("linked_roster_entry", [False, True])
def test_lk_without_huid_sees_slots_but_cannot_book(
    api: ApiEnv, db_session: Session, linked_roster_entry: bool
) -> None:
    # Нет в ростере вовсе или есть, но без учётки eXpress — одинаково «не привязан».
    employee = add_roster(db_session, None, "Петров Пётр") if linked_roster_entry else uuid.uuid4()
    h = lk_headers(employee)
    slot_id = add_slot(db_session)

    slots = api.client.get(
        "/appointments/slots", params={"from": "2026-10-05", "to": "2026-10-05"}, headers=h
    )
    assert slots.status_code == 200 and slots.json() != []
    assert api.client.get("/library/books", headers=h).status_code == 200
    assert api.client.get("/appointments/me", headers=h).json() == {"huid": None, "roles": []}

    for r in (
        api.client.post(
            "/appointments/bookings", json={"slot_id": slot_id, "kind": "psy"}, headers=h
        ),
        api.client.get("/appointments/bookings/my", headers=h),
        api.client.post(f"/library/books/{add_book(db_session)}/loan", headers=h),
        api.client.get("/library/loans/my", headers=h),
    ):
        assert (r.status_code, r.json()["code"]) == (409, "express_not_linked")


# --- один человек в двух каналах ---


def test_monthly_limit_is_shared_between_channels(api: ApiEnv, db_session: Session) -> None:
    lk = lk_headers(add_roster(db_session, HUID, "Иванова Анна"))
    bot = bot_headers(HUID)
    for day, h in ((6, lk), (7, lk), (8, bot), (9, bot)):
        assert book_slot(api, add_slot(db_session, date(2026, 10, day)), h)[0] == 201

    # Один свободный слот на оба канала: отказ — только из-за лимита.
    fifth = add_slot(db_session, date(2026, 10, 10))
    for h in (lk, bot):
        status, body = book_slot(api, fifth, h)
        assert (status, body["code"]) == (409, "monthly_limit")


def test_booking_from_bot_is_visible_and_cancellable_in_lk(
    api: ApiEnv, db_session: Session
) -> None:
    lk = lk_headers(add_roster(db_session, HUID, "Иванова Анна"))
    status, booking = book_slot(api, add_slot(db_session), bot_headers(HUID))
    assert status == 201

    my = api.client.get("/appointments/bookings/my", headers=lk).json()
    assert [b["id"] for b in my] == [booking["id"]]
    r = api.client.post(f"/appointments/bookings/{booking['id']}/cancel", headers=lk)
    assert r.json()["status"] == "cancelled_by_user"


def test_channel_and_name_snapshot_are_stored(api: ApiEnv, db_session: Session) -> None:
    employee = add_roster(db_session, HUID, "Иванова Анна")
    stranger = uuid.uuid4()  # не из КЦ: нет в ростере
    book_slot(api, add_slot(db_session, t=time(16, 0)), lk_headers(employee))
    book_slot(api, add_slot(db_session, t=time(17, 0)), bot_headers(stranger, name="Сидоров Сидор"))

    rows = db_session.execute(
        select(
            AppointmentBooking.user_huid, AppointmentBooking.user_name, AppointmentBooking.channel
        ).order_by(AppointmentBooking.id)
    ).all()
    assert [tuple(r) for r in rows] == [
        (HUID, "Иванова Анна", Channel.LK),
        (stranger, "Сидоров Сидор", Channel.EXPRESS),
    ]


def test_loan_from_lk_is_visible_in_bot(api: ApiEnv, db_session: Session) -> None:
    lk = lk_headers(add_roster(db_session, HUID, "Иванова Анна"))
    r = api.client.post(f"/library/books/{add_book(db_session)}/loan", headers=lk)
    assert r.status_code == 201

    my = api.client.get("/library/loans/my", headers=bot_headers(HUID, module="library")).json()
    assert [loan["id"] for loan in my] == [r.json()["id"]]
    assert db_session.scalar(select(LibraryLoan.channel)) == Channel.LK


# --- имена в уведомлениях ---


def test_name_from_bot_header_for_person_outside_roster(api: ApiEnv, db_session: Session) -> None:
    stranger = uuid.uuid4()
    book_slot(api, add_slot(db_session), bot_headers(stranger, name="Сидоров Сидор"))
    assert api.notifier.sent == [text_booked("Сидоров Сидор", TODAY, time(16, 0), Kind.PSY)]


def test_roster_name_wins_over_bot_header(api: ApiEnv, db_session: Session) -> None:
    add_roster(db_session, HUID, "Иванова Анна")
    r = api.client.post(
        f"/library/books/{add_book(db_session)}/loan",
        headers=bot_headers(HUID, module="library", name="Аня"),
    )
    assert api.notifier.sent == [text_loaned("Иванова Анна", "Мастер", date(2026, 10, 12))]
    assert r.status_code == 201


# --- ключи и роли ---


def test_bot_key_opens_only_its_module(api: ApiEnv) -> None:
    psy_on_library = {"X-API-Key": BOT_KEYS["appointments"], "X-User-Huid": str(HUID)}
    lib_on_appointments = {"X-API-Key": BOT_KEYS["library"], "X-User-Huid": str(HUID)}
    assert api.client.get("/library/genres", headers=psy_on_library).status_code == 401
    r = api.client.get("/appointments/me", headers=lib_on_appointments)
    assert (r.status_code, r.json()["code"]) == (401, "unauthorized")


def test_me_shows_admin_role_only_in_own_module(api: ApiEnv) -> None:
    me = api.client.get("/appointments/me", headers=bot_headers(PSY_ADMIN)).json()
    assert me == {"huid": str(PSY_ADMIN), "roles": ["psychologist"]}
    me = api.client.get("/library/me", headers=bot_headers(PSY_ADMIN, module="library")).json()
    assert me["roles"] == []
    me = api.client.get("/library/me", headers=bot_headers(LIB_ADMIN, module="library")).json()
    assert me["roles"] == ["librarian"]


def test_lk_huid_header_is_ignored(api: ApiEnv, db_session: Session) -> None:
    """Через BFF нельзя прислать чужой HUID: в ЛК HUID — только из ростера."""
    employee = add_roster(db_session, None, "Петров Пётр")
    h = {**lk_headers(employee), "X-User-Huid": str(PSY_ADMIN)}
    assert api.client.get("/appointments/me", headers=h).json() == {"huid": None, "roles": []}
