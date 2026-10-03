"""HTTP RED-метрики (rate / errors / duration) через ASGI-middleware.

Имена без префикса сервиса: Prometheus вешает job/app/role на таргет. ``path`` =
ШАБЛОН матченного маршрута (``request.scope["route"].path``), не сырой URL — иначе
кардинальность взрывается на path-параметрах. ``/metrics``|``/health``|``/ready``
исключены (шум самоскрейпа раз в 15с забил бы RPS-панель).
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response
from prometheus_client import Counter, Gauge, Histogram

from pa_booking.core.metrics import registry

_EXCLUDED = frozenset({"/metrics", "/health", "/ready"})

http_requests_total = Counter(
    "http_requests_total",
    "HTTP-запросы по методу, шаблону маршрута и коду ответа.",
    labelnames=("method", "path", "status"),
    registry=registry,
)
http_request_duration_seconds = Histogram(
    "http_request_duration_seconds",
    "Длительность обработки HTTP-запроса в секундах.",
    labelnames=("method", "path"),
    registry=registry,
)
http_requests_in_progress = Gauge(
    "http_requests_in_progress",
    "HTTP-запросы в обработке сейчас (по методу; шаблон пути известен только после роутинга).",
    labelnames=("method",),
    registry=registry,
)


def _route_template(request: Request) -> str:
    route = request.scope.get("route")
    path = getattr(route, "path", None)
    return path if isinstance(path, str) else "__unmatched__"


def install_http_metrics(app: FastAPI) -> None:
    """Повесить middleware сбора метрик на приложение."""

    @app.middleware("http")
    async def _http_metrics(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if request.url.path in _EXCLUDED:
            return await call_next(request)

        method = request.method
        http_requests_in_progress.labels(method=method).inc()
        t0 = time.monotonic()
        # finally, а не «после call_next»: при необработанном исключении запрос
        # иначе не попал бы в метрики вовсе — то есть 500-ки терялись бы ровно
        # тогда, когда они нужны.
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            return response
        finally:
            elapsed = time.monotonic() - t0
            http_requests_in_progress.labels(method=method).dec()
            template = _route_template(request)
            http_requests_total.labels(method=method, path=template, status=str(status)).inc()
            http_request_duration_seconds.labels(method=method, path=template).observe(elapsed)
