from __future__ import annotations

import uuid
from typing import Annotated
from urllib.parse import quote

import pytest
from fastapi import Depends, FastAPI
from fastapi.dependencies.utils import get_dependant
from fastapi.testclient import TestClient

from pa_booking.api.deps import admin, identified, principal, require_huid, resolve_principal
from pa_booking.core.auth import Caller, authenticate, get_caller
from pa_booking.core.config import Settings, get_settings
from pa_booking.core.errors import ApiError, install_error_handlers
from pa_booking.db.directory import LkIdentity
from pa_booking.domain.identity import Channel, Module
from pa_booking.domain.roles import Role

BFF_KEY = "bff-key"
PSY_KEY = "psy-bot-key"
LIB_KEY = "lib-bot-key"
ADMIN = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
HUID = uuid.UUID("11111111-1111-1111-1111-111111111111")
EMPLOYEE = uuid.UUID("22222222-2222-2222-2222-222222222222")

SETTINGS = Settings(
    _env_file=None,
    api_key=BFF_KEY,
    appointments_bot_api_key=PSY_KEY,
    library_bot_api_key=LIB_KEY,
    appointments_admin_huids=f"{ADMIN}",
    library_admin_huids=f" {ADMIN} ,",
)


def auth(
    key: str | None,
    *,
    user_id: str | None = None,
    huid: str | None = None,
    name: str | None = None,
) -> Caller:
    return authenticate(SETTINGS, api_key=key, user_id=user_id, user_huid=huid, user_name=name)


def code_of(exc: pytest.ExceptionInfo[ApiError]) -> tuple[int, str]:
    return exc.value.status_code, exc.value.code


# --- authenticate: ключ → канал ---


def test_bff_key_is_lk_channel_by_user_id() -> None:
    c = auth(BFF_KEY, user_id=str(EMPLOYEE), huid=str(HUID), name="X")
    # X-User-Huid/X-User-Name из ЛК игнорируются: через BFF нельзя выдать себя за HUID.
    assert c == Caller(Channel.LK, employee_id=EMPLOYEE)


def test_bot_key_is_express_channel_of_its_module() -> None:
    c = auth(PSY_KEY, user_id=str(EMPLOYEE), huid=str(HUID))
    assert c == Caller(Channel.EXPRESS, module="appointments", huid=HUID)
    assert auth(LIB_KEY, huid=str(HUID)).module == "library"


@pytest.mark.parametrize("key", [None, "", "nope"])
def test_missing_or_wrong_key_is_401(key: str | None) -> None:
    with pytest.raises(ApiError) as exc:
        auth(key, user_id=str(EMPLOYEE), huid=str(HUID))
    assert code_of(exc) == (401, "unauthorized")


def test_lk_without_valid_user_id_is_401() -> None:
    for user_id in (None, "not-a-uuid"):
        with pytest.raises(ApiError) as exc:
            auth(BFF_KEY, user_id=user_id)
        assert code_of(exc) == (401, "unauthorized")


def test_bot_without_valid_huid_is_401() -> None:
    for huid in (None, "not-a-uuid"):
        with pytest.raises(ApiError) as exc:
            auth(PSY_KEY, huid=huid, user_id=str(EMPLOYEE))
        assert code_of(exc) == (401, "unauthorized")


def test_disabled_channel_does_not_match_empty_key() -> None:
    s = Settings(_env_file=None, api_key=BFF_KEY)  # ключи ботов не заданы
    with pytest.raises(ApiError):
        authenticate(s, api_key="", user_id=None, user_huid=str(HUID), user_name=None)


def test_user_name_is_percent_decoded_and_trimmed() -> None:
    raw = quote("  Иванова   Анна ")
    assert auth(PSY_KEY, huid=str(HUID), name=raw).name == "Иванова Анна"
    assert auth(PSY_KEY, huid=str(HUID), name="").name is None
    assert auth(PSY_KEY, huid=str(HUID), name=quote("Я" * 300)).name == "Я" * 256


def test_broken_percent_encoding_does_not_fail_request() -> None:
    assert auth(PSY_KEY, huid=str(HUID), name="%D0").name == "�"


# --- resolve_principal: модуль, HUID, роли ---


def test_lk_huid_and_name_come_from_roster() -> None:
    p = resolve_principal(
        Caller(Channel.LK, employee_id=EMPLOYEE),
        module="appointments",
        settings=SETTINGS,
        lookup=lambda e: LkIdentity(HUID, "Иванова Анна") if e == EMPLOYEE else None,
    )
    assert (p.channel, p.huid, p.name, p.roles) == (Channel.LK, HUID, "Иванова Анна", frozenset())


def test_lk_without_roster_entry_or_link_has_no_huid() -> None:
    for found in (None, LkIdentity(None, "Иванова Анна")):
        p = resolve_principal(
            Caller(Channel.LK, employee_id=EMPLOYEE),
            module="library",
            settings=SETTINGS,
            lookup=lambda _e, f=found: f,
        )
        assert p.huid is None
        with pytest.raises(ApiError) as exc:
            require_huid(p)
        assert code_of(exc) == (409, "express_not_linked")


def test_bot_key_of_other_module_is_401() -> None:
    with pytest.raises(ApiError) as exc:
        resolve_principal(
            Caller(Channel.EXPRESS, module="appointments", huid=HUID),
            module="library",
            settings=SETTINGS,
            lookup=lambda _e: None,
        )
    assert code_of(exc) == (401, "unauthorized")


def test_admin_role_only_for_listed_huid_in_bot_channel() -> None:
    def roles(caller: Caller, module: Module = "appointments") -> frozenset[Role]:
        return resolve_principal(
            caller,
            module=module,
            settings=SETTINGS,
            lookup=lambda _e: LkIdentity(ADMIN, "Админ"),
        ).roles

    assert roles(Caller(Channel.EXPRESS, module="appointments", huid=ADMIN)) == {Role.PSYCHOLOGIST}
    assert roles(Caller(Channel.EXPRESS, module="library", huid=ADMIN), "library") == {
        Role.LIBRARIAN
    }
    assert roles(Caller(Channel.EXPRESS, module="appointments", huid=HUID)) == frozenset()
    # HUID админа из ЛК ролей не даёт (Р-11).
    assert roles(Caller(Channel.LK, employee_id=EMPLOYEE)) == frozenset()


# --- get_caller: заголовки по сети ---


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    install_error_handlers(app)

    @app.get("/who")
    def who(c: Annotated[Caller, Depends(get_caller)]) -> dict[str, str | None]:
        return {"channel": c.channel, "huid": c.huid and str(c.huid), "name": c.name}

    app.dependency_overrides[get_settings] = lambda: SETTINGS
    return TestClient(app)


def test_headers_reach_caller(client: TestClient) -> None:
    r = client.get(
        "/who",
        headers={"X-API-Key": PSY_KEY, "X-User-Huid": str(HUID), "X-User-Name": quote("Анна")},
    )
    assert r.json() == {"channel": "express", "huid": str(HUID), "name": "Анна"}


def test_missing_key_is_401_with_code(client: TestClient) -> None:
    r = client.get("/who", headers={"X-User-Id": str(EMPLOYEE)})
    assert r.status_code == 401
    assert r.json()["code"] == "unauthorized"


def test_non_ascii_api_key_is_401_not_500(client: TestClient) -> None:
    # Байтами: httpx не отправит str с не-ASCII, а по сети такие байты прийти могут.
    r = client.get(
        "/who", headers={"X-API-Key": "ключ".encode(), "X-User-Id": str(EMPLOYEE).encode()}
    )
    assert r.status_code == 401


@pytest.mark.parametrize("factory", [principal, identified, admin])
def test_dependency_factories_take_nothing_from_request(factory: object) -> None:
    """Регресс: аннотация с переменной замыкания становилась query-параметром (422)."""
    for module in ("appointments", "library"):
        d = get_dependant(path="/x", call=factory(module))  # type: ignore[operator]
        assert (d.query_params, d.body_params) == ([], [])
