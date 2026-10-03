"""Engine и sessionmaker из Settings — без глобального состояния (как в pa_stats)."""

from __future__ import annotations

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from pa_booking.core.config import Settings


def make_engine_from_settings(settings: Settings) -> Engine:
    dsn = settings.database_url.get_secret_value()
    if not dsn:
        raise ValueError("PA_BOOKING_DATABASE_URL is not set")
    return create_engine(dsn, pool_pre_ping=True)


def make_sessionmaker(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)
