"""Celery app factory + модульный ``celery_app`` для celery CLI.

Использование::

    celery -A pa_booking.workers.celery_app:celery_app worker -Q pa_booking
    celery -A pa_booking.workers.celery_app:celery_app beat --scheduler=redbeat.RedBeatScheduler

Паттерн — ``pa_stats/src/pa_stats/workers/celery_app.py``.
"""

from __future__ import annotations

from celery import Celery
from celery.schedules import crontab

from pa_booking.core.config import Settings, get_settings
from pa_booking.workers import metrics_server  # noqa: F401  # connects worker_process_init

# Модули с задачами. Без них воркер, запущенный по ``-A ...celery_app``, не
# импортирует sync_roster, и задача останется незарегистрированной: beat её
# опубликует, а воркер ответит «Received unregistered task».
TASK_MODULES = [
    "pa_booking.workers.sync_roster",
    "pa_booking.workers.appointments_cleanup",
]


def make_celery_app(settings: Settings) -> Celery:
    """Celery-app с RedBeat. Настройки приходят параметром — без глобальных side-effect'ов."""
    broker = settings.celery_broker_url.get_secret_value()
    # backend=None: результаты задач никто не читает, хранить их в Redis незачем.
    app = Celery("pa_booking", broker=broker, backend=None, include=TASK_MODULES)
    app.conf.update(
        # Время сервиса — всегда МСК (CLAUDE.md), отдельной настройки нет.
        timezone="Europe/Moscow",
        enable_utc=True,
        task_default_queue="pa_booking",
        task_acks_late=True,
        task_reject_on_worker_lost=True,
        task_soft_time_limit=600,
        task_time_limit=900,
        broker_transport_options={"visibility_timeout": 3600},
        beat_scheduler="redbeat.RedBeatScheduler",
        redbeat_redis_url=broker,
        # Явный {} режет фолбэк на kombu-специфичный visibility_timeout, который
        # redis-py не понимает (TypeError). Механика из pa_stats и КПД.
        redbeat_redis_options={},
        beat_schedule={
            "sync-roster": {
                "task": "pa_booking.sync_roster",
                "schedule": float(settings.roster_sync_interval_s),
            },
            # crontab — в зоне timezone выше, то есть 14:00 по Москве.
            "appointments-cleanup": {
                "task": "pa_booking.appointments_cleanup",
                "schedule": crontab(hour=14, minute=0),
            },
        },
    )
    return app


celery_app = make_celery_app(get_settings())
