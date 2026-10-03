"""Роли сервиса (спека §3, Д-5).

Роли — множество без иерархии. ``admin`` в наш набор не входит и поэтому ничего
здесь не открывает: записи к психологу — чувствительные данные, их видит только
``psychologist`` (CLAUDE.md). Коды ролей — строковый контракт с ``pa_auth_service``.
"""

from __future__ import annotations

from enum import StrEnum


class Role(StrEnum):
    PSYCHOLOGIST = "psychologist"
    LIBRARIAN = "librarian"


_BY_VALUE: dict[str, Role] = {r.value: r for r in Role}


def parse_roles(raw: str | None) -> frozenset[Role]:
    """CSV из ``X-User-Roles`` → наши роли; чужие роли отбрасываются молча."""
    if not raw:
        return frozenset()
    found = (_BY_VALUE.get(part.strip()) for part in raw.split(","))
    return frozenset(role for role in found if role is not None)
