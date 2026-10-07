from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError

from pa_booking.core.config import Settings, get_settings


def test_defaults_mean_not_configured() -> None:
    s = Settings()
    assert s.database_url.get_secret_value() == ""
    assert s.api_key.get_secret_value() == ""
    assert s.botx_cts_url == ""
    assert s.appointments_bot_id == ""
    assert s.library_notify_chat_id == ""


def test_reads_prefixed_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PA_BOOKING_API_KEY", "k")
    monkeypatch.setenv("PA_BOOKING_LIBRARY_BOT_SECRET", "s")
    s = get_settings()
    assert s.api_key.get_secret_value() == "k"
    assert s.library_bot_secret.get_secret_value() == "s"


def test_secrets_hidden_in_repr(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PA_BOOKING_APPOINTMENTS_BOT_SECRET", "top-secret")
    assert "top-secret" not in repr(Settings())


def test_admin_huids_parsed_per_module() -> None:
    a, b = uuid.uuid4(), uuid.uuid4()
    s = Settings(_env_file=None, appointments_admin_huids=f"{a}, {b},", library_admin_huids="")
    assert s.admin_huids("appointments") == {a, b}
    assert s.admin_huids("library") == frozenset()


def test_typo_in_admin_huids_fails_at_startup() -> None:
    with pytest.raises(ValidationError, match="не HUID"):
        Settings(_env_file=None, library_admin_huids="not-a-huid")


def test_bot_keys_must_differ_from_bff_and_each_other() -> None:
    with pytest.raises(ValidationError, match="различаться"):
        Settings(_env_file=None, api_key="k", appointments_bot_api_key="k")
    with pytest.raises(ValidationError, match="различаться"):
        Settings(_env_file=None, appointments_bot_api_key="k", library_bot_api_key="k")
    # Пустые ключи — выключенные каналы, совпадением не считаются.
    Settings(_env_file=None, api_key="k")
