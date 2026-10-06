from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from pa_booking.db.directory import (
    employees_by_huid,
    names_by_id,
    save_snapshot,
    snapshot_age_seconds,
)
from pa_booking.directory.sync import EmployeeRow

pytestmark = pytest.mark.db
NOW = datetime(2026, 10, 5, 9, 0, tzinfo=UTC)


def test_snapshot_replaces_previous(db_session: Session) -> None:
    a, b = uuid.uuid4(), uuid.uuid4()
    save_snapshot(db_session, (EmployeeRow(a, "Анна", False),), now=NOW)
    db_session.commit()
    save_snapshot(db_session, (EmployeeRow(b, "Борис", False),), now=NOW)
    db_session.commit()
    assert names_by_id(db_session, [a, b]) == {b: "Борис"}


def test_snapshot_age(db_session: Session) -> None:
    assert snapshot_age_seconds(db_session, now=NOW) is None
    save_snapshot(db_session, (EmployeeRow(uuid.uuid4(), "Анна", False),), now=NOW)
    db_session.commit()
    assert snapshot_age_seconds(db_session, now=NOW + timedelta(minutes=5)) == 300


def test_snapshot_keeps_express_huid(db_session: Session) -> None:
    a, b = uuid.uuid4(), uuid.uuid4()
    huid = "11111111-1111-1111-1111-111111111111"
    rows = (EmployeeRow(a, "Анна", False, huid), EmployeeRow(b, "Борис", False))
    save_snapshot(db_session, rows, now=NOW)
    db_session.commit()
    assert employees_by_huid(db_session) == {huid: a}
