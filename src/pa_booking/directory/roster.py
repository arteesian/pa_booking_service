"""Клиент ростера auth-сервиса (``GET /v1/directory/roster``).

Нам из ростера нужны только ФИО по ``employee_id`` — для уведомлений и выгрузок.
Контракт ответа дублируется локальными Pydantic-моделями намеренно — мы не
зависим от кода auth. Лишние поля (``reference_groups``, ``subgroup``,
``aliases``…) Pydantic игнорирует. Механика — копия ``pa_overtimes/directory``.
"""

from __future__ import annotations

import uuid
from typing import Protocol

from pydantic import BaseModel, ValidationError

ROSTER_PATH = "/v1/directory/roster"


class DirectoryFetchError(RuntimeError):
    """auth недоступен, вернул не-200, неожиданную схему или пустой ростер."""


class HttpClient(Protocol):
    def get(self, path: str, *, headers: dict[str, str]) -> tuple[int, bytes]: ...


class RosterPerson(BaseModel):
    """Сотрудник из ростера."""

    employee_id: uuid.UUID
    name: str
    dismissed: bool


class RosterSnapshot(BaseModel):
    # operators обязателен: его отсутствие — поломка контракта, а не пустой ростер.
    operators: list[RosterPerson]
    # Дефолт пустой — старый auth без этого поля (как в overtimes).
    employees: list[RosterPerson] = []


def fetch_roster_snapshot(client: HttpClient, *, api_key: str) -> RosterSnapshot:
    """Забрать снимок ростера. Любой сбой → :class:`DirectoryFetchError`."""
    try:
        status_code, body = client.get(ROSTER_PATH, headers={"X-API-Key": api_key})
    except Exception as exc:  # сетевой сбой / DNS / connection refused
        raise DirectoryFetchError(f"auth-service недоступен: {exc}") from exc
    if status_code != 200:
        raise DirectoryFetchError(f"roster API вернул {status_code}: {body[:200]!r}")
    try:
        return RosterSnapshot.model_validate_json(body)
    except ValidationError as exc:
        raise DirectoryFetchError(f"неожиданная схема ответа roster API: {exc}") from exc


def make_httpx_client(base_url: str, *, timeout: float = 30.0) -> HttpClient:
    """Production-клиент на httpx."""
    import httpx

    class _HttpxClient:
        def __init__(self) -> None:
            self._client = httpx.Client(base_url=base_url, timeout=timeout)

        def get(self, path: str, *, headers: dict[str, str]) -> tuple[int, bytes]:
            resp = self._client.get(path, headers=headers)
            return resp.status_code, resp.content

    return _HttpxClient()
