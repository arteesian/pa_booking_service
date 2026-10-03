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
