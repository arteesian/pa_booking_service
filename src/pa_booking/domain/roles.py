"""Роли сервиса (спека §3.1, Д-5, Р-10).

Роли — множество без иерархии. Источник — не токен, а HUID из ``*_ADMIN_HUIDS`` в
env сервиса, и только в канале бота: админки в ЛК нет (Р-11). Записи к психологу —
чувствительные данные, их видит только ``psychologist`` (CLAUDE.md).
"""

from __future__ import annotations

import uuid
from collections.abc import Collection
from enum import StrEnum

from pa_booking.domain.identity import Channel, Module


class Role(StrEnum):
    PSYCHOLOGIST = "psychologist"
    LIBRARIAN = "librarian"


# Админская роль модуля: её получает HUID из списка админов этого модуля.
ADMIN_ROLE: dict[Module, Role] = {
    "appointments": Role.PSYCHOLOGIST,
    "library": Role.LIBRARIAN,
}


def roles_for(
    channel: Channel,
    huid: uuid.UUID | None,
    *,
    module: Module,
    admin_huids: Collection[uuid.UUID],
) -> frozenset[Role]:
    """Роли пользователя в модуле: админская — только в канале бота и по списку."""
    if channel is Channel.EXPRESS and huid is not None and huid in admin_huids:
        return frozenset({ADMIN_ROLE[module]})
    return frozenset()
