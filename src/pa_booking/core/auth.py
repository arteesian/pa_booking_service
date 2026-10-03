"""Меш-аутентификация M2M (CLAUDE.md, «Архитектурные рамки»).

BFF уже провалидировал пользователя по JWKS и шлёт M2M-ключ ``X-API-Key`` плюс
проброшенный контекст ``X-User-Id`` / ``X-User-Roles``. JWT здесь не валидируется.
Паттерн — ``pa_overtimes/core/auth.py``.
"""

from __future__ import annotations

import hmac
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Header

from pa_booking.core.config import Settings, get_settings
from pa_booking.core.errors import ApiError
from pa_booking.domain.roles import Role, parse_roles


@dataclass(frozen=True)
class MeshPrincipal:
    """Пользователь, от имени которого BFF выполняет запрос."""

    employee_id: uuid.UUID
    roles: frozenset[Role]


def get_current_user(
    settings: Annotated[Settings, Depends(get_settings)],
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
    x_user_id: Annotated[str | None, Header(alias="X-User-Id")] = None,
    x_user_roles: Annotated[str | None, Header(alias="X-User-Roles")] = None,
) -> MeshPrincipal:
    """Проверить M2M-ключ и извлечь принципала из проброшенного контекста."""
    expected = settings.api_key.get_secret_value()
    # Байты, а не str: compare_digest на str с не-ASCII бросает TypeError → 500.
    if (
        not expected
        or not x_api_key
        or not hmac.compare_digest(x_api_key.encode(), expected.encode())
    ):
        raise ApiError(401, "unauthorized", "Нет или неверный X-API-Key")
    try:
        employee_id = uuid.UUID(x_user_id or "")
    except ValueError as exc:
        raise ApiError(401, "unauthorized", "Нет или неверный X-User-Id") from exc
    return MeshPrincipal(employee_id=employee_id, roles=parse_roles(x_user_roles))


def require_role(role: Role) -> Callable[..., MeshPrincipal]:
    """Зависимость FastAPI: пропустить только принципала с ролью ``role``."""

    def _dependency(
        principal: Annotated[MeshPrincipal, Depends(get_current_user)],
    ) -> MeshPrincipal:
        if role not in principal.roles:
            raise ApiError(403, "forbidden", "Недостаточно прав")
        return principal

    return _dependency
