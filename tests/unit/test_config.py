from __future__ import annotations

import pytest

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
