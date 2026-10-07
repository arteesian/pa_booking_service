"""Миграция 0005 на данных: брони из ЛК получают HUID из ростера, без HUID — отказ.

Каждый тест — в своей свежей базе того же контейнера: общая ``pg_engine`` уже на
``head``, а здесь нужно остановиться на 0004, налить строки и подняться.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Connection, Engine, create_engine, text

from tests.integration.conftest import ROOT

pytestmark = pytest.mark.db

EMPLOYEE = uuid.UUID("11111111-1111-1111-1111-111111111111")
UNLINKED = uuid.UUID("22222222-2222-2222-2222-222222222222")
HUID = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")


@pytest.fixture
def fresh_url(pg_engine: Engine) -> Iterator[str]:
    name = f"m0005_{uuid.uuid4().hex[:8]}"
    admin = pg_engine.execution_options(isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f"CREATE DATABASE {name}"))
    url = pg_engine.url.set(database=name).render_as_string(hide_password=False)
    try:
        yield url
    finally:
        with admin.connect() as conn:
            conn.execute(text(f"DROP DATABASE {name} WITH (FORCE)"))


def _alembic(url: str) -> Config:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return cfg


def _seed_0004(conn: Connection, employee_id: uuid.UUID) -> None:
    """Сотрудники ростера, слот, бронь и книга с выдачей — по схеме 0004."""
    conn.execute(
        text(
            "INSERT INTO directory_employees (employee_id, full_name, express_huid) VALUES"
            " (:e, 'Иванова Анна', :h), (:u, 'Петров Пётр', NULL)"
        ),
        {"e": EMPLOYEE, "h": str(HUID).upper(), "u": UNLINKED},
    )
    slot_id = conn.execute(
        text(
            "INSERT INTO appointment_slots (slot_date, slot_time)"
            " VALUES ('2026-10-20', '16:00') RETURNING id"
        )
    ).scalar_one()
    conn.execute(
        text(
            "INSERT INTO appointment_bookings (slot_id, employee_id, kind, status)"
            " VALUES (:s, :e, 'psy', 'active')"
        ),
        {"s": slot_id, "e": employee_id},
    )
    book_id = conn.execute(
        text(
            "INSERT INTO library_books (genre, author, title, description)"
            " VALUES ('Роман', 'Булгаков', 'Мастер', '—') RETURNING id"
        )
    ).scalar_one()
    conn.execute(
        text(
            "INSERT INTO library_loans (book_id, employee_id, starts_on, due_on)"
            " VALUES (:b, :e, '2026-10-05', '2026-10-12')"
        ),
        {"b": book_id, "e": employee_id},
    )


def test_rows_of_linked_employee_move_to_huid_and_back(fresh_url: str) -> None:
    cfg = _alembic(fresh_url)
    command.upgrade(cfg, "0004_express_huid")
    engine = create_engine(fresh_url)
    try:
        with engine.begin() as conn:
            _seed_0004(conn, EMPLOYEE)

        command.upgrade(cfg, "0005_user_huid")
        with engine.connect() as conn:
            for table in ("appointment_bookings", "library_loans"):
                row = conn.execute(text(f"SELECT user_huid, user_name, channel FROM {table}")).one()
                assert tuple(row) == (HUID, "Иванова Анна", "lk"), table

        command.downgrade(cfg, "0004_express_huid")
        with engine.connect() as conn:
            for table in ("appointment_bookings", "library_loans"):
                assert conn.execute(text(f"SELECT employee_id FROM {table}")).scalar_one() == (
                    EMPLOYEE
                )
    finally:
        engine.dispose()


def test_row_without_huid_in_roster_stops_migration(fresh_url: str) -> None:
    cfg = _alembic(fresh_url)
    command.upgrade(cfg, "0004_express_huid")
    engine = create_engine(fresh_url)
    try:
        with engine.begin() as conn:
            _seed_0004(conn, UNLINKED)
        with pytest.raises(RuntimeError, match="appointment_bookings: у строк"):
            command.upgrade(cfg, "0005_user_huid")
        # Миграция транзакционная: схема осталась на 0004, данные целы.
        with engine.connect() as conn:
            assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == (
                "0004_express_huid"
            )
            assert conn.execute(
                text("SELECT employee_id FROM appointment_bookings")
            ).scalar_one() == (UNLINKED)
    finally:
        engine.dispose()
