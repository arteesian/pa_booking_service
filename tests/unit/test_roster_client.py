from __future__ import annotations

import json
import uuid

import pytest

from pa_booking.directory.roster import ROSTER_PATH, DirectoryFetchError, fetch_roster_snapshot

EMP = str(uuid.uuid4())


class _Client:
    def __init__(self, status: int = 200, body: bytes = b"", exc: Exception | None = None):
        self.status, self.body, self.exc = status, body, exc
        self.calls: list[tuple[str, dict[str, str]]] = []

    def get(self, path: str, *, headers: dict[str, str]) -> tuple[int, bytes]:
        self.calls.append((path, headers))
        if self.exc:
            raise self.exc
        return self.status, self.body


def _body(**extra: object) -> bytes:
    payload: dict[str, object] = {
        "reference_groups": {},
        "operators": [
            {"employee_id": EMP, "name": "Иванов И.", "subgroup": "g", "dismissed": False}
        ],
        "employees": [],
    }
    payload.update(extra)
    return json.dumps(payload).encode()


def test_parses_snapshot_and_ignores_extra_fields() -> None:
    snap = fetch_roster_snapshot(_Client(body=_body(aliases={})), api_key="k")
    assert str(snap.operators[0].employee_id) == EMP


def test_sends_api_key_to_roster_path() -> None:
    client = _Client(body=_body())
    fetch_roster_snapshot(client, api_key="k")
    assert client.calls == [(ROSTER_PATH, {"X-API-Key": "k"})]


def test_non_200_raises() -> None:
    with pytest.raises(DirectoryFetchError):
        fetch_roster_snapshot(_Client(status=503, body=b"down"), api_key="k")


def test_missing_operators_field_raises() -> None:
    """Схема без operators — это не «пустой ростер», а поломка контракта."""
    with pytest.raises(DirectoryFetchError):
        fetch_roster_snapshot(_Client(body=b'{"employees": []}'), api_key="k")


def test_network_failure_raises_domain_error() -> None:
    with pytest.raises(DirectoryFetchError):
        fetch_roster_snapshot(_Client(exc=OSError("refused")), api_key="k")
