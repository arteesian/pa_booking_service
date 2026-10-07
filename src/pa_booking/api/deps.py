"""Общие зависимости FastAPI: сессия БД, пользователь, текущее время.

Пользователь — второй слой аутентификации (первый — ``core/auth.py``): из
:class:`Caller` получаем HUID (для ЛК — из ростера), имя и роли модуля (спека §3.1).
Зависимости — фабрики на модуль, три уровня:

- :func:`principal` — смотреть слоты и каталог; HUID может не быть (ЛК без привязки);
- :func:`identified` — личные действия; нет HUID → 409 ``express_not_linked`` (Д-10);
- :func:`admin` — админка модуля: канал бота и HUID из ``*_ADMIN_HUIDS`` (Р-10, Р-11).
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from pa_booking.core.auth import Caller, get_caller
from pa_booking.core.config import Settings, get_settings
from pa_booking.core.errors import ApiError
from pa_booking.db.directory import LkIdentity, lk_identity
from pa_booking.domain.identity import Channel, Module
from pa_booking.domain.roles import ADMIN_ROLE, Role, roles_for


def get_session(request: Request) -> Iterator[Session]:
    """Сессия на запрос. Коммитит вызывающая ручка — транзакция явная."""
    sessionmaker_ = getattr(request.app.state, "sessionmaker", None)
    if sessionmaker_ is None:
        raise ApiError(503, "db_not_configured", "База данных не настроена")
    with sessionmaker_() as session:
        yield session


def get_now() -> datetime:
    """Текущее время запроса. Тесты подменяют зависимость — без патчинга часов."""
    return datetime.now(UTC)


DbSession = Annotated[Session, Depends(get_session)]
Now = Annotated[datetime, Depends(get_now)]


@dataclass(frozen=True)
class Principal:
    """Пользователь в модуле. ``name`` — снимок для брони: бот — ``X-User-Name``,
    ЛК — ФИО из ростера."""

    channel: Channel
    huid: uuid.UUID | None
    name: str | None
    roles: frozenset[Role]


@dataclass(frozen=True)
class Identified:
    """Пользователь с HUID — может записываться и бронировать."""

    channel: Channel
    huid: uuid.UUID
    name: str | None
    roles: frozenset[Role]


def resolve_principal(
    caller: Caller,
    *,
    module: Module,
    settings: Settings,
    lookup: Callable[[uuid.UUID], LkIdentity | None],
) -> Principal:
    """Caller → Principal. Ключ бота другого модуля — 401. Чистая: ростер — ``lookup``."""
    if caller.module is not None and caller.module != module:
        raise ApiError(401, "unauthorized", "Ключ не подходит для этого раздела")
    if caller.channel is Channel.LK:
        assert caller.employee_id is not None  # гарантирует authenticate
        found = lookup(caller.employee_id)
        huid, name = (None, None) if found is None else (found.huid, found.full_name)
    else:
        huid, name = caller.huid, caller.name
    roles = roles_for(caller.channel, huid, module=module, admin_huids=settings.admin_huids(module))
    return Principal(caller.channel, huid, name, roles)


def require_huid(p: Principal) -> Identified:
    if p.huid is None:
        raise ApiError(
            409,
            "express_not_linked",
            "Учётная запись eXpress не привязана к профилю — обратитесь к администратору ЛК",
        )
    return Identified(p.channel, p.huid, p.name, p.roles)


def principal(module: Module) -> Callable[..., Principal]:
    def _dependency(
        caller: Annotated[Caller, Depends(get_caller)],
        settings: Annotated[Settings, Depends(get_settings)],
        session: DbSession,
    ) -> Principal:
        return resolve_principal(
            caller, module=module, settings=settings, lookup=lambda e: lk_identity(session, e)
        )

    return _dependency


# В identified/admin зависимость — значением по умолчанию, а не в Annotated: из-за
# ``from __future__ import annotations`` аннотация — строка, и FastAPI вычисляет её по
# глобальным именам модуля; ``module`` из замыкания там не виден → ForwardRef не
# разрешается, и ``p`` молча становится query-параметром (422).


def identified(module: Module) -> Callable[..., Identified]:
    def _dependency(p: Principal = Depends(principal(module))) -> Identified:  # noqa: B008
        return require_huid(p)

    return _dependency


def admin(module: Module) -> Callable[..., Identified]:
    def _dependency(p: Principal = Depends(principal(module))) -> Identified:  # noqa: B008
        # Роль есть только в канале бота (roles_for), так что из ЛК сюда не пройти.
        if ADMIN_ROLE[module] not in p.roles:
            raise ApiError(403, "forbidden", "Недостаточно прав")
        return require_huid(p)

    return _dependency
