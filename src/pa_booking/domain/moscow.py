"""Время сервиса — Москва (CLAUDE.md): «сегодня», 14:00, границы месяца, сроки броней.

Общий для модулей записей и библиотеки — они друг друга не импортируют.
"""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

MSK = ZoneInfo("Europe/Moscow")


def today_msk(now: datetime) -> date:
    """Дата по Москве для aware ``now`` в любой зоне."""
    return now.astimezone(MSK).date()
