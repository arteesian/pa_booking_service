from __future__ import annotations

import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from pa_booking.api.appointments import get_appointments_notifier
from pa_booking.api.deps import get_now
from pa_booking.api.library import get_library_notifier
from pa_booking.app import create_app
from pa_booking.core.config import Settings, get_settings
from pa_booking.db.models import Base, DirectoryEmployee
from pa_booking.domain.appointments import MSK
from pa_booking.domain.identity import Module
from pa_booking.notify.botx import FakeNotifier

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="session")
def pg_engine() -> Iterator[Engine]:
    """Postgres в testcontainers; схема — через миграции, а не create_all.

    Так тесты ловят расхождение миграций с моделями.
    """
    from testcontainers.community.postgres import PostgresContainer

    with PostgresContainer("postgres:16-alpine", driver="psycopg") as container:
        url = container.get_connection_url()
        cfg = Config(str(ROOT / "alembic.ini"))
        cfg.set_main_option("script_location", str(ROOT / "alembic"))
        # ConfigParser: «%» — спецсимвол, экранируем (get_main_option вернёт исходный URL).
        cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
        command.upgrade(cfg, "head")
        engine = create_engine(url, pool_pre_ping=True)
        try:
            yield engine
        finally:
            engine.dispose()


@pytest.fixture
def db_session(pg_engine: Engine) -> Iterator[Session]:
    """Сессия на тест; после теста таблицы чистятся — тесты не видят друг друга."""
    with Session(pg_engine) as session:
        yield session
        session.rollback()
        for table in reversed(Base.metadata.sorted_tables):
            session.execute(table.delete())
        session.commit()


API_KEY = "test-mesh-key"  # BFF (ЛК)
BOT_KEYS: dict[Module, str] = {"appointments": "test-psy-bot", "library": "test-lib-bot"}
# HUID админов: специалист — записей, библиотекарь — библиотеки (спека §3.1).
PSY_ADMIN = uuid.UUID("33333333-3333-3333-3333-333333333333")
LIB_ADMIN = uuid.UUID("44444444-4444-4444-4444-444444444444")


@dataclass
class Clock:
    """Подменяемое «сейчас» для ручек (зависимость get_now)."""

    now: datetime


@dataclass
class ApiEnv:
    client: TestClient
    clock: Clock
    notifier: FakeNotifier


@pytest.fixture
def api(pg_engine: Engine, db_session: Session) -> ApiEnv:
    """Приложение на тестовой БД: сессия на запрос, как в проде (нужно для гонок).

    ``db_session`` здесь ради очистки таблиц после теста.
    """
    app = create_app()
    app.state.sessionmaker = sessionmaker(bind=pg_engine, expire_on_commit=False)
    env = ApiEnv(TestClient(app), Clock(datetime(2026, 10, 5, 10, 0, tzinfo=MSK)), FakeNotifier())
    settings = Settings(
        _env_file=None,
        api_key=API_KEY,
        appointments_bot_api_key=BOT_KEYS["appointments"],
        library_bot_api_key=BOT_KEYS["library"],
        appointments_admin_huids=str(PSY_ADMIN),
        library_admin_huids=str(LIB_ADMIN),
    )
    app.dependency_overrides[get_settings] = lambda: settings
    # Через env: тест может перевести часы или подменить notifier на падающий.
    app.dependency_overrides[get_now] = lambda: env.clock.now
    app.dependency_overrides[get_appointments_notifier] = lambda: env.notifier
    app.dependency_overrides[get_library_notifier] = lambda: env.notifier
    return env


def lk_headers(employee_id: uuid.UUID) -> dict[str, str]:
    """Запрос из ЛК: BFF шлёт employee_id, HUID сервис ищет в ростере."""
    return {"X-API-Key": API_KEY, "X-User-Id": str(employee_id)}


def bot_headers(
    huid: uuid.UUID, *, module: Module = "appointments", name: str | None = None
) -> dict[str, str]:
    """Запрос из бота модуля: HUID и (необязательно) имя из eXpress."""
    h = {"X-API-Key": BOT_KEYS[module], "X-User-Huid": str(huid)}
    if name is not None:
        h["X-User-Name"] = quote(name)
    return h


def add_roster(
    db: Session, huid: uuid.UUID | None, name: str, employee_id: uuid.UUID | None = None
) -> uuid.UUID:
    """Сотрудник в снимке ростера (ЛК); ``huid=None`` — eXpress не привязан. → employee_id."""
    employee_id = employee_id or uuid.uuid4()
    db.add(
        DirectoryEmployee(
            employee_id=employee_id,
            full_name=name,
            express_huid=None if huid is None else str(huid),
        )
    )
    db.commit()
    return employee_id
