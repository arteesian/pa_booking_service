"""Точка входа FastAPI-приложения."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from fastapi import FastAPI, status
from fastapi.responses import JSONResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import text

from pa_booking import __version__
from pa_booking.api import appointments, library
from pa_booking.core.config import get_settings
from pa_booking.core.errors import install_error_handlers
from pa_booking.core.http_metrics import install_http_metrics
from pa_booking.core.logging import configure_logging
from pa_booking.core.metrics import registry
from pa_booking.db.directory import snapshot_age_seconds
from pa_booking.db.session import make_engine_from_settings, make_sessionmaker


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Eager-создание engine при наличии DSN, dispose на shutdown."""
    settings = get_settings()
    if settings.database_url.get_secret_value():
        engine = make_engine_from_settings(settings)
        app.state.engine = engine
        app.state.sessionmaker = make_sessionmaker(engine)
    else:
        app.state.engine = None
        app.state.sessionmaker = None
    try:
        yield
    finally:
        if app.state.engine is not None:
            app.state.engine.dispose()


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(level=settings.log_level, debug=settings.debug)

    app = FastAPI(
        title="PA Booking Service",
        version=__version__,
        description="Записи к психологу и на консультацию МКР, бронирование книг библиотеки.",
        lifespan=lifespan,
    )
    install_http_metrics(app)
    install_error_handlers(app)
    app.include_router(appointments.router)
    app.include_router(library.router)

    @app.get("/health", tags=["ops"])
    def health() -> dict[str, str]:
        """Liveness: процесс жив. Зависимости не проверяются намеренно."""
        return {"status": "ok", "version": __version__}

    @app.get("/ready", tags=["ops"])
    def ready() -> JSONResponse:
        """Readiness: БД доступна и снимок ростера не протух."""
        checks: dict[str, str] = {}
        sessionmaker_ = getattr(app.state, "sessionmaker", None)
        if sessionmaker_ is None:
            checks["database"] = "not configured"
        else:
            try:
                with sessionmaker_() as session:
                    session.execute(text("SELECT 1"))
                    checks["database"] = "ok"
                    age = snapshot_age_seconds(session, now=datetime.now(UTC))
                if age is None:
                    # Снимка нет вообще — ФИО в уведомлениях и выгрузках взять неоткуда.
                    checks["roster"] = "never synced"
                elif age > settings.roster_max_age_s:
                    checks["roster"] = f"stale: {int(age)}s"
                else:
                    checks["roster"] = "ok"
            # Наружу отдаём только имя класса ошибки: детали DSN в /ready не место.
            except Exception as exc:
                checks["database"] = f"error: {type(exc).__name__}"
        if any(v != "ok" for v in checks.values()):
            return JSONResponse(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                content={"detail": checks, "code": "not_ready"},
            )
        return JSONResponse(content=checks)

    @app.get("/metrics", include_in_schema=False)
    def metrics() -> Response:
        return Response(generate_latest(registry), media_type=CONTENT_TYPE_LATEST)

    return app


app = create_app()
