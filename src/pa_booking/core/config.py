"""Конфигурация сервиса.

Читается из переменных окружения (префикс ``PA_BOOKING_``) или из ``.env``.
Пустое значение означает «не настроено»: сервис поднимается, а зависящая от него
функциональность отражается в ``/ready`` (БД, ростер) или в метрике сбоев
уведомлений (BotX).
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="PA_BOOKING_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    debug: bool = False
    log_level: str = "INFO"

    # DSN Postgres (postgresql+psycopg://user:pass@host:5432/pa_booking).
    database_url: SecretStr = SecretStr("")
    # M2M-ключ mesh: им BFF стучится к нам.
    api_key: SecretStr = SecretStr("")

    # --- pa_auth_service: ростер (ФИО по employee_id) ---
    auth_base_url: str = ""
    auth_api_key: SecretStr = SecretStr("")
    roster_sync_interval_s: int = Field(default=900, ge=60)
    # Возраст снимка, после которого /ready считает сервис деградировавшим.
    roster_max_age_s: int = Field(default=3600, ge=60)

    # --- BotX (спека §3): аккаунты ботов шлют в служебные чаты ---
    botx_cts_url: str = ""
    botx_timeout_s: float = Field(default=15.0, gt=0)
    # Аккаунт psy_bot_v2 → чат специалиста.
    appointments_bot_id: str = ""
    appointments_bot_secret: SecretStr = SecretStr("")
    appointments_notify_chat_id: str = ""
    # Аккаунт Library_bot → чат библиотеки.
    library_bot_id: str = ""
    library_bot_secret: SecretStr = SecretStr("")
    library_notify_chat_id: str = ""

    # --- Celery / RedBeat ---
    celery_broker_url: SecretStr = SecretStr("redis://localhost:6379/0")
    # Порт embedded-экспортёра воркера: job-метрики живут в его процессе.
    worker_metrics_port: int = 9100


@lru_cache
def get_settings() -> Settings:
    return Settings()
