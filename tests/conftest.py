from __future__ import annotations

import os
from collections.abc import Iterator

import pytest

from pa_booking.core.config import Settings, get_settings


@pytest.fixture(autouse=True)
def _isolate_settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Тесты не зависят от .env и окружения разработчика.

    Закрываем оба источника настроек: переменные ``PA_BOOKING_*`` и файл ``.env``.
    Иначе заведённый по инструкции .env отправит юнит-тесты в настоящую БД/BotX.
    """
    for name in list(os.environ):
        if name.startswith("PA_BOOKING_"):
            monkeypatch.delenv(name)
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
