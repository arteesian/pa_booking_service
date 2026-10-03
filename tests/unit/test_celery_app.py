from __future__ import annotations

from pa_booking.core.config import Settings
from pa_booking.workers.celery_app import TASK_MODULES, make_celery_app


def test_task_module_is_included() -> None:
    """Без include воркер не импортирует sync_roster.

    Задача тогда не регистрируется: beat её публикует, воркер отвечает
    «Received unregistered task», и ростер молча перестаёт синкаться.
    """
    app = make_celery_app(Settings(_env_file=None))
    assert "pa_booking.workers.sync_roster" in TASK_MODULES
    assert list(app.conf.include) == TASK_MODULES


def test_task_registers_after_import() -> None:
    from pa_booking.workers import sync_roster

    assert sync_roster.sync_roster.name == "pa_booking.sync_roster"


def test_beat_schedule_points_at_existing_task_name() -> None:
    """Имя в расписании должно совпадать с именем задачи — опечатка тут молчалива."""
    from pa_booking.workers import sync_roster

    app = make_celery_app(Settings(_env_file=None))
    scheduled = app.conf.beat_schedule["sync-roster"]["task"]
    assert scheduled == sync_roster.sync_roster.name


def test_queue_matches_worker_flag() -> None:
    """docker-compose запускает воркер с -Q pa_booking; очередь должна совпасть."""
    app = make_celery_app(Settings(_env_file=None))
    assert app.conf.task_default_queue == "pa_booking"
