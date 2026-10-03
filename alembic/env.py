"""Alembic: DSN и метаданные берём из кода сервиса, а не из alembic.ini."""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from pa_booking.core.config import get_settings
from pa_booking.db.models import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _dsn() -> str:
    # URL, заданный явно в конфиге Alembic (так делают интеграционные тесты),
    # важнее настроек сервиса.
    explicit = config.get_main_option("sqlalchemy.url")
    if explicit:
        return explicit
    dsn = get_settings().database_url.get_secret_value()
    if not dsn:
        raise RuntimeError("PA_BOOKING_DATABASE_URL is not set")
    return dsn


def run_migrations_offline() -> None:
    context.configure(
        url=_dsn(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    config.set_main_option("sqlalchemy.url", _dsn())
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
