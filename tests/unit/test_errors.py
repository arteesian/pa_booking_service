from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

from pa_booking.core.errors import ApiError, install_error_handlers


class _Body(BaseModel):
    slot_id: int


def _client() -> TestClient:
    app = FastAPI()
    install_error_handlers(app)

    @app.post("/thing")
    def thing(body: _Body) -> dict[str, int]:
        return {"slot_id": body.slot_id}

    @app.get("/busy")
    def busy() -> None:
        raise ApiError(409, "slot_unavailable", "Слот уже занят")

    return TestClient(app)


def test_api_error_shape() -> None:
    r = _client().get("/busy")
    assert r.status_code == 409
    assert r.json() == {"detail": "Слот уже занят", "code": "slot_unavailable"}


def test_validation_error_shape() -> None:
    r = _client().post("/thing", json={"slot_id": "x"})
    assert r.status_code == 422
    assert r.json()["code"] == "validation_error"
    assert "slot_id" in r.json()["detail"]
    assert set(r.json()) == {"detail", "code"}


def test_unknown_route_shape() -> None:
    r = _client().get("/nope")
    assert r.status_code == 404
    assert r.json() == {"detail": "Не найдено", "code": "not_found"}
