from __future__ import annotations

from datetime import UTC, datetime

import pytest
from celery.schedules import crontab

from pa_booking.core.config import Settings
from pa_booking.workers.celery_app import TASK_MODULES, make_celery_app


def test_cleanup_module_is_included() -> None:
    assert "pa_booking.workers.appointments_cleanup" in TASK_MODULES


def test_cleanup_scheduled_daily_at_14_moscow() -> None:
    from pa_booking.workers import appointments_cleanup

    app = make_celery_app(Settings(_env_file=None))
    entry = app.conf.beat_schedule["appointments-cleanup"]
    assert entry["task"] == appointments_cleanup.appointments_cleanup.name
    assert entry["schedule"] == crontab(hour=14, minute=0)
    # crontab считается в зоне Celery — она обязана быть МСК, а не UTC контейнера.
    assert app.conf.timezone == "Europe/Moscow"


@pytest.mark.parametrize(
    ("now", "expected"),
    [
        (datetime(2026, 10, 5, 10, 59, tzinfo=UTC), False),  # 13:59 МСК
        (datetime(2026, 10, 5, 11, 0, tzinfo=UTC), True),  # 14:00 МСК
    ],
)
def test_catch_up_on_worker_start_only_after_14(now: datetime, expected: bool) -> None:
    from pa_booking.workers.appointments_cleanup import catch_up_due

    assert catch_up_due(now) is expected
