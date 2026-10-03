from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from pa_booking.app import create_app
from pa_booking.db.models import DirectorySyncState


class _FakeSession:
    """Двойник Session для /ready: БД отвечает, снимок — какой зададим."""

    def __init__(self, state: DirectorySyncState | None) -> None:
        self._state = state

    def execute(self, *_args: object, **_kwargs: object) -> None:
        return None

    def get(self, _model: object, _pk: object) -> DirectorySyncState | None:
        return self._state

    def __enter__(self) -> _FakeSession:
        return self

    def __exit__(self, *_exc: object) -> bool:
        return False

    def close(self) -> None:
        return None


def _sync_state(*, age: timedelta) -> DirectorySyncState:
    return DirectorySyncState(id=1, synced_at=datetime.now(UTC) - age, employees_count=1)


def test_health_is_open_and_ok() -> None:
    """/health не требует меш-ключа — это liveness для оркестратора."""
    with TestClient(create_app()) as client:
        r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_ready_without_dsn_reports_not_ready() -> None:
    """Без PA_BOOKING_DATABASE_URL сервис читать не может — не готов."""
    with TestClient(create_app()) as client:
        r = client.get("/ready")
    assert r.status_code == 503
    assert r.json()["detail"]["database"] == "not configured"
    assert r.json()["code"] == "not_ready"


def test_ready_reports_never_synced_roster() -> None:
    """БД жива, но синка ростера не было — отдавать ФИО и линии нечем."""
    app = create_app()
    with TestClient(app) as client:
        app.state.sessionmaker = lambda: _FakeSession(None)
        r = client.get("/ready")
    assert r.status_code == 503
    assert r.json()["detail"] == {"database": "ok", "roster": "never synced"}


def test_ready_reports_stale_roster() -> None:
    """Снимок старше roster_max_age_s (по умолчанию час) — сервис деградировал."""
    app = create_app()
    with TestClient(app) as client:
        app.state.sessionmaker = lambda: _FakeSession(_sync_state(age=timedelta(hours=5)))
        r = client.get("/ready")
    assert r.status_code == 503
    assert r.json()["detail"]["roster"].startswith("stale: ")


def test_ready_ok_with_fresh_roster() -> None:
    app = create_app()
    with TestClient(app) as client:
        app.state.sessionmaker = lambda: _FakeSession(_sync_state(age=timedelta(minutes=5)))
        r = client.get("/ready")
    assert r.status_code == 200
    assert r.json() == {"database": "ok", "roster": "ok"}


def test_metrics_exposes_prometheus_text() -> None:
    """Имена — по конвенции флота: без префикса сервиса, его вешает Prometheus."""
    with TestClient(create_app()) as client:
        client.get("/nope")
        r = client.get("/metrics")
    assert r.status_code == 200
    assert "text/plain" in r.headers["content-type"]
    assert "http_requests_total" in r.text
    assert "python_gc_objects_collected_total" in r.text
