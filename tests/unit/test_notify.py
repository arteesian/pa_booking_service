from __future__ import annotations

import json
import uuid
from collections.abc import Callable

import httpx
import jwt
import pytest
from pydantic import SecretStr

from pa_booking.core.config import Settings
from pa_booking.core.metrics import notify_failures_total
from pa_booking.notify.botx import (
    BotxNotifier,
    BotxSendError,
    BotxTarget,
    FakeNotifier,
    botx_target,
    build_botx_jwt,
    make_notifier,
    notify_safely,
)

BOT, CHAT = uuid.uuid4(), uuid.uuid4()
SECRET = "s" * 32
SYNC_PATH = "/api/v4/botx/notifications/direct/sync"


def _target() -> BotxTarget:
    return BotxTarget(
        cts_url="https://cts.example.corp/", bot_id=BOT, secret=SecretStr(SECRET), chat_id=CHAT
    )


def _notifier(handler: Callable[[httpx.Request], httpx.Response]) -> BotxNotifier:
    return BotxNotifier(_target(), timeout_s=5, transport=httpx.MockTransport(handler))


def _ok(_request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"status": "ok", "result": {"sync_id": str(uuid.uuid4())}})


def _failures(module: str) -> float:
    return notify_failures_total.labels(module=module)._value.get()


def test_jwt_matches_botx_v2_format() -> None:
    token = build_botx_jwt(bot_id=BOT, host="cts.example.corp", secret=SECRET, now=1_700_000_000)
    claims = jwt.decode(
        token,
        SECRET,
        algorithms=["HS256"],
        audience="cts.example.corp",
        options={"verify_exp": False, "verify_nbf": False},
    )
    assert claims["iss"] == str(BOT)
    assert claims["version"] == 2
    assert claims["exp"] - claims["iat"] == 60
    assert claims["nbf"] == claims["iat"] == 1_700_000_000
    assert claims["jti"]


def test_posts_sync_notification_with_signed_jwt() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return _ok(request)

    text = "✅ Иванов Иван\nЗаписался на 05.10.2026 в 11:00\nПсихолог"
    _notifier(handler).send(text)

    assert [(r.method, r.url.host, r.url.path) for r in seen] == [
        ("POST", "cts.example.corp", SYNC_PATH)
    ]
    body = json.loads(seen[0].content)
    assert body["group_chat_id"] == str(CHAT)
    assert body["notification"]["status"] == "ok"
    assert body["notification"]["body"] == text
    token = seen[0].headers["Authorization"].removeprefix("Bearer ")
    claims = jwt.decode(token, SECRET, algorithms=["HS256"], audience="cts.example.corp")
    assert claims["iss"] == str(BOT)


def test_http_error_raises() -> None:
    with pytest.raises(BotxSendError, match="500"):
        _notifier(lambda _r: httpx.Response(500, text="boom")).send("x")


def test_error_status_in_body_raises() -> None:
    """Синхронный endpoint сообщает о сбое доставки телом, а не HTTP-кодом."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "error", "reason": "bot_is_not_a_chat_member"})

    with pytest.raises(BotxSendError, match="bot_is_not_a_chat_member"):
        _notifier(handler).send("x")


def test_redirect_is_an_error_with_status_and_location() -> None:
    """httpx не следует редиректам: пустой 3xx — не успех, и куда звали — видно в ошибке."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(301, headers={"Location": "https://cts.example.corp/api/v4/x"})

    with pytest.raises(BotxSendError, match=r"301.*https://cts\.example\.corp/api/v4/x"):
        _notifier(handler).send("x")


def test_empty_body_error_names_status() -> None:
    with pytest.raises(BotxSendError, match="HTTP 204"):
        _notifier(lambda _r: httpx.Response(204)).send("x")


def test_network_error_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(BotxSendError, match="ConnectError"):
        _notifier(handler).send("x")


def test_notify_safely_swallows_and_counts() -> None:
    before = _failures("library")
    notify_safely(FakeNotifier(fail=True), "x", module="library")
    assert _failures("library") == before + 1


def test_notify_safely_counts_missing_notifier() -> None:
    before = _failures("appointments")
    notify_safely(None, "x", module="appointments")
    assert _failures("appointments") == before + 1


def test_notify_safely_delivers() -> None:
    fake = FakeNotifier()
    notify_safely(fake, "привет", module="library")
    assert fake.sent == ["привет"]


def test_target_is_none_until_fully_configured() -> None:
    s = Settings(_env_file=None, botx_cts_url="https://cts", library_bot_id=str(BOT))
    assert botx_target(s, "library") is None
    assert make_notifier(s, "library") is None


def test_target_per_module() -> None:
    s = Settings(
        _env_file=None,
        botx_cts_url="https://cts",
        library_bot_id=str(BOT),
        library_bot_secret=SecretStr("s"),
        library_notify_chat_id=str(CHAT),
    )
    target = botx_target(s, "library")
    assert target is not None and target.chat_id == CHAT
    assert botx_target(s, "appointments") is None
