from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from pa_booking.core.config import Settings
from pa_booking.notify.botx import Module, make_notifier

pytestmark = pytest.mark.external


@pytest.mark.parametrize("module", ["appointments", "library"])
def test_sends_to_service_chat(module: Module, monkeypatch: pytest.MonkeyPatch) -> None:
    # Снимаем изоляцию из tests/conftest.py: настройки — из корневого .env.
    monkeypatch.setitem(Settings.model_config, "env_file", ".env")
    notifier = make_notifier(Settings(), module)
    assert notifier is not None, f"BotX для {module} не настроен в .env"
    stamp = datetime.now(ZoneInfo("Europe/Moscow")).strftime("%d.%m.%Y %H:%M")
    notifier.send(f"[ТЕСТ ЛК, external-тест, {stamp} МСК] ✅ проверка уведомлений\nстрока 2")
