"""Уведомления в служебные чаты eXpress через BotX (спека Р-5, §5.4).

Шлём от аккаунтов прод-ботов (``psy_bot_v2`` → записи, ``Library_bot`` → библиотека)
синхронным методом ``POST /api/v4/botx/notifications/direct/sync``: результат
приходит в ответе, колбэков нет — нам не нужен приёмник вебхуков. Тот же метод
использует ``express_notif``. JWT собираем сами — формат как у
``pybotx.auth.build_botx_jwt_v2``.

``Notifier.send`` при сбое бросает; вызывающий код зовёт ``notify_safely``, который
сбой логирует и считает, но наружу не пускает — операция уже закоммичена.
"""

from __future__ import annotations

import secrets
import time
import uuid
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlparse

import httpx
import jwt
import structlog
from pydantic import SecretStr

from pa_booking.core.config import Settings
from pa_booking.core.metrics import notify_failures_total
from pa_booking.domain.identity import Module

log = structlog.get_logger(__name__)

SYNC_NOTIFICATION_PATH = "/api/v4/botx/notifications/direct/sync"
JWT_TTL_S = 60


class Notifier(Protocol):
    def send(self, text: str) -> None: ...


class BotxSendError(RuntimeError):
    """BotX не принял уведомление: сеть, HTTP-ошибка или ``status: error`` в теле."""


@dataclass(frozen=True)
class BotxTarget:
    cts_url: str
    bot_id: uuid.UUID
    secret: SecretStr
    chat_id: uuid.UUID


def botx_target(settings: Settings, module: Module) -> BotxTarget | None:
    """Куда и от чьего имени слать для модуля; None — BotX для модуля не настроен."""
    if module == "appointments":
        bot_id, secret, chat_id = (
            settings.appointments_bot_id,
            settings.appointments_bot_secret,
            settings.appointments_notify_chat_id,
        )
    else:
        bot_id, secret, chat_id = (
            settings.library_bot_id,
            settings.library_bot_secret,
            settings.library_notify_chat_id,
        )
    if not (settings.botx_cts_url and bot_id and secret.get_secret_value() and chat_id):
        return None
    return BotxTarget(
        cts_url=settings.botx_cts_url,
        bot_id=uuid.UUID(bot_id),
        secret=secret,
        chat_id=uuid.UUID(chat_id),
    )


def build_botx_jwt(*, bot_id: uuid.UUID, host: str, secret: str, now: int) -> str:
    """JWT авторизации бота в BotX (версия 2): подписывается секретом бота локально."""
    payload = {
        "iss": str(bot_id),
        "aud": host,
        "exp": now + JWT_TTL_S,
        "nbf": now,
        "iat": now,
        "jti": secrets.token_hex(12),
        "version": 2,
    }
    return jwt.encode(payload, secret, algorithm="HS256")


class BotxNotifier:
    """Отправка текста в чат от имени бота."""

    def __init__(
        self,
        target: BotxTarget,
        *,
        timeout_s: float,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        host = urlparse(target.cts_url).hostname
        if not host:
            raise ValueError("не удалось разобрать хост из PA_BOOKING_BOTX_CTS_URL")
        self._target = target
        self._host = host
        self._url = target.cts_url.rstrip("/") + SYNC_NOTIFICATION_PATH
        self._timeout_s = timeout_s
        self._transport = transport

    def send(self, text: str) -> None:
        token = build_botx_jwt(
            bot_id=self._target.bot_id,
            host=self._host,
            secret=self._target.secret.get_secret_value(),
            now=int(time.time()),
        )
        payload = {
            "group_chat_id": str(self._target.chat_id),
            "notification": {"status": "ok", "body": text},
        }
        try:
            with httpx.Client(timeout=self._timeout_s, transport=self._transport) as client:
                response = client.post(
                    self._url, json=payload, headers={"Authorization": f"Bearer {token}"}
                )
        except httpx.HTTPError as exc:
            raise BotxSendError(f"{type(exc).__name__}: {exc}") from exc
        # Не только 4xx/5xx: httpx не следует редиректам, и пустой 3xx — тоже провал.
        if not response.is_success:
            location = response.headers.get("Location", "")
            raise BotxSendError(
                f"BotX ответил HTTP {response.status_code}"
                + (f", Location: {location}" if location else "")
                + f": {response.text[:200]}"
            )
        try:
            body = response.json()
        except ValueError as exc:
            raise BotxSendError(
                f"BotX вернул не JSON (HTTP {response.status_code}, "
                f"Content-Type: {response.headers.get('Content-Type', '')}): "
                f"{response.text[:200]!r}"
            ) from exc
        if body.get("status") != "ok":
            raise BotxSendError(f"BotX отклонил уведомление: {body.get('reason')}")


class FakeNotifier:
    """Двойник для тестов: копит тексты или падает по заказу."""

    def __init__(self, *, fail: bool = False) -> None:
        self.sent: list[str] = []
        self._fail = fail

    def send(self, text: str) -> None:
        if self._fail:
            raise BotxSendError("FakeNotifier: сбой отправки")
        self.sent.append(text)


def make_notifier(settings: Settings, module: Module) -> Notifier | None:
    target = botx_target(settings, module)
    if target is None:
        return None
    return BotxNotifier(target, timeout_s=settings.botx_timeout_s)


def notify_safely(notifier: Notifier | None, text: str, *, module: Module) -> None:
    """Отправить, а при любом сбое — warning в лог и +1 в метрику. Не бросает.

    Текст в лог не пишем: в нём ФИО и тип записи (чувствительные данные).
    """
    if notifier is None:
        log.warning("notify_skipped", module=module, reason="botx_not_configured")
        notify_failures_total.labels(module=module).inc()
        return
    try:
        notifier.send(text)
    except Exception as exc:
        log.warning("notify_failed", module=module, error=f"{type(exc).__name__}: {exc}")
        notify_failures_total.labels(module=module).inc()
