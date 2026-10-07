"""Схемы, общие для модулей (инфраструктура пользователя — спека §6а)."""

from __future__ import annotations

import uuid

from pydantic import BaseModel

from pa_booking.domain.roles import Role


class MeOut(BaseModel):
    """Кто я в модуле: бот решает по ``roles``, показывать ли админское меню.

    ``huid`` пуст у пользователя ЛК без привязанной учётки eXpress (Д-10).
    """

    huid: uuid.UUID | None
    roles: list[Role]
