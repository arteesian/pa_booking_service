from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from pa_booking.db.directory import (
    LkIdentity,
    display_names,
    lk_identity,
    names_by_huid,
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
    assert lk_identity(db_session, a) is None
    assert lk_identity(db_session, b) == LkIdentity(None, "Борис")


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
    assert lk_identity(db_session, a) == LkIdentity(uuid.UUID(huid), "Анна")


H1 = uuid.UUID("11111111-1111-1111-1111-111111111111")
H2 = uuid.UUID("22222222-2222-2222-2222-222222222222")


def test_lk_identity_and_names_by_huid(db_session: Session) -> None:
    a = uuid.uuid4()
    save_snapshot(db_session, (EmployeeRow(a, "Анна", False, str(H1)),), now=NOW)
    db_session.commit()
    assert lk_identity(db_session, a) == LkIdentity(H1, "Анна")
    assert names_by_huid(db_session, [H1, H2]) == {H1: "Анна"}


def test_display_names_roster_then_snapshot_then_huid(db_session: Session) -> None:
    save_snapshot(db_session, (EmployeeRow(uuid.uuid4(), "Анна", False, str(H1)),), now=NOW)
    db_session.commit()
    h3 = uuid.uuid4()
    names = display_names(db_session, [(H1, "Старое имя"), (H2, None), (H2, "Борис"), (h3, None)])
    assert names == {H1: "Анна", H2: "Борис", h3: str(h3)}
