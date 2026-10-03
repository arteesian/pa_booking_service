"""Prometheus-метрики сервиса — один реестр на процесс, чтобы /metrics был полным.

Конвенция флота Личного Кабинета (``pa_bff/deploy/observability/README.md``):
HTTP RED-метрики — без префикса сервиса (job/app/role вешает Prometheus на
таргет), доменные и job-метрики — с префиксом ``booking_``.
"""

from __future__ import annotations

from prometheus_client import (
    CollectorRegistry,
    Counter,
    Gauge,
    GCCollector,
    Histogram,
    PlatformCollector,
    ProcessCollector,
)

registry = CollectorRegistry()

# CPU, resident memory, open FDs (process_*), python_info, python_gc_* —
# иначе их нет в кастомном реестре.
ProcessCollector(registry=registry)
PlatformCollector(registry=registry)
GCCollector(registry=registry)

# --- Синк снимка ростера (живёт в процессе ВОРКЕРА) ---

roster_sync_runs_total = Counter(
    "booking_roster_sync_runs_total",
    "Терминальные исходы синка снимка ростера из auth.",
    labelnames=("status",),
    registry=registry,
)

roster_sync_duration_seconds = Histogram(
    "booking_roster_sync_duration_seconds",
    "Длительность одного синка ростера в секундах.",
    registry=registry,
)

roster_sync_last_success_timestamp_seconds = Gauge(
    "booking_roster_sync_last_success_timestamp_seconds",
    "Wall-clock время последнего успешного синка ростера.",
    registry=registry,
)

roster_employees = Gauge(
    "booking_roster_employees",
    "Число сотрудников в последнем сохранённом снимке ростера.",
    registry=registry,
)

# --- Уведомления BotX (спека §5.4): сбой не откатывает операцию, но виден здесь ---

notify_failures_total = Counter(
    "booking_notify_failures_total",
    "Неотправленные уведомления в служебные чаты eXpress.",
    labelnames=("module",),
    registry=registry,
)
