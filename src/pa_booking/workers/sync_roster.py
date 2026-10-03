"""Периодический синк снимка ростера auth (ФИО для уведомлений и выгрузок).

Ядро синка (``sync_roster_once``) — обычная функция с явными зависимостями,
поэтому тестируется без Celery и без сети.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime

import structlog
from sqlalchemy.orm import Session

from pa_booking.core.config import get_settings
from pa_booking.core.metrics import (
    roster_employees,
    roster_sync_duration_seconds,
    roster_sync_last_success_timestamp_seconds,
    roster_sync_runs_total,
)
from pa_booking.db.directory import save_snapshot
from pa_booking.db.session import make_engine_from_settings, make_sessionmaker
from pa_booking.directory.roster import (
    DirectoryFetchError,
    HttpClient,
    fetch_roster_snapshot,
    make_httpx_client,
)
from pa_booking.directory.sync import flatten
from pa_booking.workers.celery_app import celery_app

log = structlog.get_logger(__name__)


def sync_roster_once(session: Session, client: HttpClient, *, api_key: str, now: datetime) -> int:
    """Забрать ростер и заменить снимок. Возвращает число сотрудников.

    Пустой ростер не сохраняем: это сбой на стороне auth, а не «все уволились» —
    иначе пропали бы ФИО во всех уведомлениях и выгрузках.
    """
    rows = flatten(fetch_roster_snapshot(client, api_key=api_key))
    if not rows:
        raise DirectoryFetchError("ростер пуст — снимок не обновляем")
    save_snapshot(session, rows, now=now)
    session.commit()
    return len(rows)


# Декоратор Celery не типизирован: под --strict mypy иначе объявит всю функцию
# нетипизированной. Точечное подавление — здесь, а не ослабление настройки.
@celery_app.task(name="pa_booking.sync_roster")  # type: ignore[untyped-decorator]
def sync_roster() -> int:
    """Задача Celery. Сбой auth логируем и выходим: сервис живёт на старом снимке."""
    settings = get_settings()
    engine = make_engine_from_settings(settings)
    started = time.monotonic()
    try:
        with make_sessionmaker(engine)() as session:
            count = sync_roster_once(
                session,
                make_httpx_client(settings.auth_base_url),
                api_key=settings.auth_api_key.get_secret_value(),
                now=datetime.now(UTC),
            )
    except DirectoryFetchError as exc:
        roster_sync_runs_total.labels(status="failed").inc()
        log.warning("roster_sync_failed", error=str(exc))
        return 0
    except Exception:
        # БД/сеть/что угодно неожиданное: прогон всё равно провален — счётчик
        # должен это видеть, а исключение уходит наверх к retry-политике Celery.
        roster_sync_runs_total.labels(status="failed").inc()
        raise
    else:
        roster_sync_runs_total.labels(status="succeeded").inc()
        roster_sync_last_success_timestamp_seconds.set_to_current_time()
        # Размер снимка — отдельным gauge: резкое падение числа сотрудников
        # (неполный ответ auth) видно на графике раньше, чем в уведомлениях.
        roster_employees.set(count)
        log.info("roster_synced", employees=count)
        return count
    finally:
        roster_sync_duration_seconds.observe(time.monotonic() - started)
        engine.dispose()
