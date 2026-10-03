"""Ежедневная чистка 14:00 МСК: свободные слоты на сегодня удаляются (спека §4.1).

Как в боте (``slot_cleanup.py``): после 14:00 записаться на сегодня уже нельзя,
специалист получает в чат список удалённых окон. Если воркер стартовал после 14:00
(рестарт, деплой), прогон делается сразу — иначе плановый 14:00 этого дня потерян.
Повторный прогон безопасен: удалённые слоты второй раз не находятся.
"""

from __future__ import annotations

from datetime import UTC, datetime, time
from typing import Any

import structlog
from celery.signals import worker_ready
from sqlalchemy.orm import Session

from pa_booking.core.config import get_settings
from pa_booking.db.appointments import remove_free_slots_on
from pa_booking.db.session import make_engine_from_settings, make_sessionmaker
from pa_booking.domain.appointments import cleanup_date, text_cleanup
from pa_booking.notify.botx import Notifier, make_notifier, notify_safely
from pa_booking.workers.celery_app import celery_app

log = structlog.get_logger(__name__)

TASK_NAME = "pa_booking.appointments_cleanup"


def run_cleanup(session: Session, notifier: Notifier | None, *, now: datetime) -> list[time]:
    """Удалить свободные слоты сегодняшней даты (с 14:00 МСК). → время удалённых."""
    day = cleanup_date(now)
    if day is None:
        return []
    removed = remove_free_slots_on(session, day, now=now)
    session.commit()
    if removed:
        notify_safely(notifier, text_cleanup(removed), module="appointments")
    return removed


def catch_up_due(now: datetime) -> bool:
    """Нужен ли догоняющий прогон при старте воркера."""
    return cleanup_date(now) is not None


@celery_app.task(name=TASK_NAME)  # type: ignore[untyped-decorator]
def appointments_cleanup() -> int:
    """Задача Celery: число удалённых слотов."""
    settings = get_settings()
    engine = make_engine_from_settings(settings)
    try:
        with make_sessionmaker(engine)() as session:
            removed = run_cleanup(
                session, make_notifier(settings, "appointments"), now=datetime.now(UTC)
            )
    finally:
        engine.dispose()
    log.info("appointments_cleanup_done", removed=len(removed))
    return len(removed)


@worker_ready.connect  # type: ignore[untyped-decorator]
def _catch_up_on_start(**_kwargs: Any) -> None:
    if catch_up_due(datetime.now(UTC)):
        celery_app.send_task(TASK_NAME)
