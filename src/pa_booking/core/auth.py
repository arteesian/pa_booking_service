"""M2M-аутентификация: ключ → канал (спека §3.1).

Первый слой, без БД. ``X-API-Key`` определяет канал:

- ключ BFF — ЛК; пользователь — ``X-User-Id`` (JWT BFF уже провалидировал по JWKS);
- ключ бота модуля — eXpress; пользователь — ``X-User-Huid``, имя — ``X-User-Name``.

Заголовки чужого канала игнорируются: через BFF нельзя прислать ``X-User-Huid`` и
выдать себя за другого. HUID для ЛК, роли и проверку модуля делает второй слой —
``api/deps.py``.
"""

from __future__ import annotations

import hmac
import uuid
from dataclasses import dataclass
from typing import Annotated
from urllib.parse import unquote

from fastapi import Depends, Header
from pydantic import SecretStr

from pa_booking.core.config import Settings, get_settings
from pa_booking.core.errors import ApiError
from pa_booking.domain.identity import Channel, Module

USER_NAME_MAX = 256  # = длина user_name в БД


@dataclass(frozen=True)
class Caller:
    """Кто звонит. У ЛК есть ``employee_id``, у бота — ``module``, ``huid`` и ``name``."""

    channel: Channel
    module: Module | None = None  # модуль ключа бота; ЛК — все модули
    employee_id: uuid.UUID | None = None
    huid: uuid.UUID | None = None
    name: str | None = None


def _key_matches(given: bytes, expected: SecretStr) -> bool:
    value = expected.get_secret_value()
    # Байты, а не str: compare_digest на str с не-ASCII бросает TypeError → 500.
    return bool(value) and hmac.compare_digest(given, value.encode())


def _uuid(raw: str | None, header: str) -> uuid.UUID:
    try:
        return uuid.UUID(raw or "")
    except ValueError as exc:
        raise ApiError(401, "unauthorized", f"Нет или неверный {header}") from exc


def decode_user_name(raw: str | None) -> str | None:
    """``X-User-Name``: percent-encoded UTF-8 (кириллицу заголовки HTTP не несут)."""
    if not raw:
        return None
    # errors="replace": кривой байт портит только имя в уведомлении, а не запрос.
    name = " ".join(unquote(raw, errors="replace").split())
    return name[:USER_NAME_MAX] or None


def authenticate(
    settings: Settings,
    *,
    api_key: str | None,
    user_id: str | None,
    user_huid: str | None,
    user_name: str | None,
) -> Caller:
    """Проверить ключ и разобрать заголовки его канала. Нет совпадения — 401."""
    given = (api_key or "").encode()
    if given:
        if _key_matches(given, settings.api_key):
            return Caller(Channel.LK, employee_id=_uuid(user_id, "X-User-Id"))
        module: Module
        for module in ("appointments", "library"):
            if _key_matches(given, settings.bot_api_key(module)):
                return Caller(
                    Channel.EXPRESS,
                    module=module,
                    huid=_uuid(user_huid, "X-User-Huid"),
                    name=decode_user_name(user_name),
                )
    raise ApiError(401, "unauthorized", "Нет или неверный X-API-Key")


def get_caller(
    settings: Annotated[Settings, Depends(get_settings)],
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
    x_user_id: Annotated[str | None, Header(alias="X-User-Id")] = None,
    x_user_huid: Annotated[str | None, Header(alias="X-User-Huid")] = None,
    x_user_name: Annotated[str | None, Header(alias="X-User-Name")] = None,
) -> Caller:
    """Зависимость FastAPI над :func:`authenticate`."""
    return authenticate(
        settings,
        api_key=x_api_key,
        user_id=x_user_id,
        user_huid=x_user_huid,
        user_name=x_user_name,
    )
