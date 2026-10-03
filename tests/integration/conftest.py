from __future__ import annotations

import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

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
from pa_booking.db.models import Base
from pa_booking.domain.appointments import MSK
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


API_KEY = "test-mesh-key"


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
    app.dependency_overrides[get_settings] = lambda: Settings(_env_file=None, api_key=API_KEY)
    # Через env: тест может перевести часы или подменить notifier на падающий.
    app.dependency_overrides[get_now] = lambda: env.clock.now
    app.dependency_overrides[get_appointments_notifier] = lambda: env.notifier
    app.dependency_overrides[get_library_notifier] = lambda: env.notifier
    return env


def headers(*, user_id: uuid.UUID, roles: str = "operator") -> dict[str, str]:
    return {"X-API-Key": API_KEY, "X-User-Id": str(user_id), "X-User-Roles": roles}
