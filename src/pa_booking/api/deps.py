"""Общие зависимости FastAPI: сессия БД и принципал."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from pa_booking.core.auth import MeshPrincipal, get_current_user, require_role
from pa_booking.core.errors import ApiError
from pa_booking.domain.roles import Role


def get_session(request: Request) -> Iterator[Session]:
    """Сессия на запрос. Коммитит вызывающая ручка — транзакция явная."""
    sessionmaker_ = getattr(request.app.state, "sessionmaker", None)
    if sessionmaker_ is None:
        raise ApiError(503, "db_not_configured", "База данных не настроена")
    with sessionmaker_() as session:
        yield session


DbSession = Annotated[Session, Depends(get_session)]
Principal = Annotated[MeshPrincipal, Depends(get_current_user)]
PsychologistPrincipal = Annotated[MeshPrincipal, Depends(require_role(Role.PSYCHOLOGIST))]
LibrarianPrincipal = Annotated[MeshPrincipal, Depends(require_role(Role.LIBRARIAN))]
