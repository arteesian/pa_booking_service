from __future__ import annotations

import uuid
from typing import Annotated

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from pa_booking.core.auth import MeshPrincipal, get_current_user, require_role
from pa_booking.core.config import Settings, get_settings
from pa_booking.core.errors import install_error_handlers
from pa_booking.domain.roles import Role

API_KEY = "test-mesh-key"


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    install_error_handlers(app)

    @app.get("/who")
    def who(p: Annotated[MeshPrincipal, Depends(get_current_user)]) -> dict[str, object]:
        return {"id": str(p.employee_id), "roles": sorted(p.roles)}

    @app.get("/psy")
    def psy(
        p: Annotated[MeshPrincipal, Depends(require_role(Role.PSYCHOLOGIST))],
    ) -> dict[str, bool]:
        return {"ok": True}

    app.dependency_overrides[get_settings] = lambda: Settings(_env_file=None, api_key=API_KEY)
    return TestClient(app)


def _headers(*, key: str = API_KEY, user: str | None = None, roles: str = "") -> dict[str, str]:
    return {"X-API-Key": key, "X-User-Id": user or str(uuid.uuid4()), "X-User-Roles": roles}


def test_missing_api_key_is_401_with_code(client: TestClient) -> None:
    r = client.get("/who", headers={"X-User-Id": str(uuid.uuid4())})
    assert r.status_code == 401
    assert r.json()["code"] == "unauthorized"
    assert isinstance(r.json()["detail"], str)


def test_wrong_api_key_is_401(client: TestClient) -> None:
    assert client.get("/who", headers=_headers(key="nope")).status_code == 401


def test_non_ascii_api_key_is_401_not_500(client: TestClient) -> None:
    # Байтами: httpx не отправит str с не-ASCII, а по сети такие байты прийти могут.
    r = client.get(
        "/who",
        headers={"X-API-Key": "ключ".encode(), "X-User-Id": str(uuid.uuid4()).encode()},
    )
    assert r.status_code == 401


def test_missing_user_id_is_401(client: TestClient) -> None:
    assert client.get("/who", headers={"X-API-Key": API_KEY}).status_code == 401


def test_non_uuid_user_id_is_401(client: TestClient) -> None:
    assert client.get("/who", headers=_headers(user="not-a-uuid")).status_code == 401


def test_valid_headers_give_principal(client: TestClient) -> None:
    user = str(uuid.uuid4())
    r = client.get("/who", headers=_headers(user=user, roles="librarian,operator"))
    assert r.status_code == 200
    assert r.json() == {"id": user, "roles": ["librarian"]}


def test_role_gate_rejects_without_role(client: TestClient) -> None:
    r = client.get("/psy", headers=_headers(roles="librarian"))
    assert r.status_code == 403
    assert r.json()["code"] == "forbidden"


def test_admin_does_not_open_psychologist_routes(client: TestClient) -> None:
    assert client.get("/psy", headers=_headers(roles="admin")).status_code == 403


def test_role_gate_accepts_role(client: TestClient) -> None:
    assert client.get("/psy", headers=_headers(roles="psychologist")).status_code == 200
