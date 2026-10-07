"""Конфигурация сервиса.

Читается из переменных окружения (префикс ``PA_BOOKING_``) или из ``.env``.
Пустое значение означает «не настроено»: сервис поднимается, а зависящая от него
функциональность отражается в ``/ready`` (БД, ростер) или в метрике сбоев
уведомлений (BotX).
"""

from __future__ import annotations

import uuid
from functools import lru_cache
from typing import Self

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from pa_booking.domain.identity import Module


def parse_huids(raw: str) -> frozenset[uuid.UUID]:
    """CSV HUID'ов → множество. Пустые элементы пропускаются, не-UUID — ``ValueError``."""
    huids: set[uuid.UUID] = set()
    for part in raw.split(","):
        value = part.strip()
        if not value:
            continue
        try:
            huids.add(uuid.UUID(value))
        except ValueError as exc:
            raise ValueError(f"не HUID: {value!r}") from exc
    return frozenset(huids)


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
    # M2M-ключи (спека §3.1): канал определяется по ключу. Пустой — канал выключен.
    api_key: SecretStr = SecretStr("")  # BFF (ЛК)
    appointments_bot_api_key: SecretStr = SecretStr("")  # psy_bot_v2 — только /appointments
    library_bot_api_key: SecretStr = SecretStr("")  # Library_bot — только /library
    # HUID админов модуля через запятую (Р-10): psychologist / librarian в канале бота.
    appointments_admin_huids: str = ""
    library_admin_huids: str = ""

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

    @field_validator("appointments_admin_huids", "library_admin_huids")
    @classmethod
    def _huids_are_uuids(cls, value: str) -> str:
        parse_huids(value)  # опечатка в списке админов — отказ при старте, а не молча
        return value

    @model_validator(mode="after")
    def _keys_are_distinct(self) -> Self:
        # Совпавшие ключи сделали бы канал неоднозначным: ключ BFF открыл бы
        # X-User-Huid, то есть выдачу себя за любого пользователя.
        keys = [
            k.get_secret_value()
            for k in (self.api_key, self.appointments_bot_api_key, self.library_bot_api_key)
            if k.get_secret_value()
        ]
        if len(keys) != len(set(keys)):
            raise ValueError("M2M-ключи BFF и ботов должны различаться")
        return self

    def bot_api_key(self, module: Module) -> SecretStr:
        return (
            self.appointments_bot_api_key if module == "appointments" else self.library_bot_api_key
        )

    def admin_huids(self, module: Module) -> frozenset[uuid.UUID]:
        raw = (
            self.appointments_admin_huids if module == "appointments" else self.library_admin_huids
        )
        return parse_huids(raw)


@lru_cache
def get_settings() -> Settings:
    return Settings()
