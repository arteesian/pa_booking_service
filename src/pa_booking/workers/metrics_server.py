"""Embedded prometheus-экспортёр воркера.

Job-метрики (``booking_roster_sync_*``) инкрементируются в процессе
celery-воркера, а не в api — на api-``/metrics`` их нет. Поэтому воркер поднимает
свой ``/metrics`` на ``worker_metrics_port``. Хук — ``worker_process_init``: при
prefork задачи выполняются в ДОЧЕРНЕМ процессе, метрики живут там же. Текущий
compose — ``--concurrency=1`` → дочерний ровно один, порт занимается однократно.
Если concurrency поднимут >1, нужен prometheus multiprocess mode (общий каталог).

Паттерн — ``pa_stats/src/pa_stats/workers/metrics_server.py``.
"""

from __future__ import annotations

import structlog
from celery.signals import worker_process_init
from prometheus_client import start_http_server

from pa_booking.core.config import get_settings
from pa_booking.core.metrics import registry

log = structlog.get_logger(__name__)


@worker_process_init.connect  # type: ignore[untyped-decorator]
def _start_metrics_server(**_kwargs: object) -> None:
    port = get_settings().worker_metrics_port
    start_http_server(port, registry=registry)
    log.info("worker.metrics_server_started", port=port)
