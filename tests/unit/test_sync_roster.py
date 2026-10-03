from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pytest

from pa_booking.directory.roster import DirectoryFetchError
from pa_booking.workers.sync_roster import sync_roster_once


class _Client:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._body = json.dumps(payload).encode()

    def get(self, path: str, *, headers: dict[str, str]) -> tuple[int, bytes]:
        return 200, self._body


class _Session:
    def __init__(self) -> None:
        self.committed = False
        self.executed = 0

    def execute(self, *_a: object, **_k: object) -> None:
        self.executed += 1

    def add_all(self, *_a: object) -> None: ...

    def add(self, *_a: object) -> None: ...

    def get(self, *_a: object) -> None:
        return None

    def commit(self) -> None:
        self.committed = True


def test_empty_roster_does_not_wipe_snapshot() -> None:
    """Пустой ответ auth — сбой, а не «все уволились»: старые ФИО остаются."""
    session = _Session()
    with pytest.raises(DirectoryFetchError):
        sync_roster_once(
            session,  # type: ignore[arg-type]
            _Client({"operators": [], "employees": []}),
            api_key="k",
            now=datetime.now(UTC),
        )
    assert session.executed == 0
    assert not session.committed


def test_task_closes_auth_client_even_on_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """Задача крутится раз в 15 минут в долгоживущем воркере: клиент не должен течь."""
    from contextlib import nullcontext

    from pa_booking.workers import sync_roster as task_module

    closed: list[bool] = []

    class _ClosableClient(_Client):
        def close(self) -> None:
            closed.append(True)

    class _Engine:
        def dispose(self) -> None: ...

    def _fail(*_a: object, **_k: object) -> int:
        raise DirectoryFetchError("auth лежит")

    monkeypatch.setattr(task_module, "make_engine_from_settings", lambda _s: _Engine())
    monkeypatch.setattr(task_module, "make_sessionmaker", lambda _e: lambda: nullcontext(None))
    monkeypatch.setattr(task_module, "make_httpx_client", lambda _url: _ClosableClient({}))
    monkeypatch.setattr(task_module, "sync_roster_once", _fail)

    assert task_module.sync_roster() == 0
    assert closed == [True]


def test_httpx_client_close_closes_connection_pool() -> None:
    from pa_booking.directory.roster import make_httpx_client

    client = make_httpx_client("http://auth.invalid")
    client.close()
    assert client._client.is_closed  # type: ignore[attr-defined]


def test_non_empty_roster_is_saved_and_committed() -> None:
    session = _Session()
    count = sync_roster_once(
        session,  # type: ignore[arg-type]
        _Client(
            {
                "operators": [
                    {
                        "employee_id": "7b6f0c1e-3f5a-4a7e-9a55-1d2c3b4a5e6f",
                        "name": "Анна",
                        "dismissed": False,
                    }
                ]
            }
        ),
        api_key="k",
        now=datetime.now(UTC),
    )
    assert count == 1
    assert session.committed
