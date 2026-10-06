from __future__ import annotations

import uuid

from pa_booking.directory.roster import RosterPerson, RosterSnapshot
from pa_booking.directory.sync import EmployeeRow, flatten

A, B = uuid.uuid4(), uuid.uuid4()


def test_employees_are_the_base_and_operators_fill_gaps() -> None:
    snap = RosterSnapshot(
        employees=[RosterPerson(employee_id=A, name="Анна", dismissed=False)],
        operators=[
            RosterPerson(employee_id=A, name="Анна (подгруппа)", dismissed=False),
            RosterPerson(employee_id=B, name="Борис", dismissed=True),
        ],
    )
    assert set(flatten(snap)) == {
        EmployeeRow(employee_id=A, full_name="Анна", dismissed=False, express_huid=None),
        EmployeeRow(employee_id=B, full_name="Борис", dismissed=True, express_huid=None),
    }


def test_operator_in_several_subgroups_is_one_row() -> None:
    person = RosterPerson(employee_id=A, name="Анна", dismissed=False)
    assert len(flatten(RosterSnapshot(operators=[person, person]))) == 1


H = "11111111-1111-1111-1111-111111111111"


def test_express_huid_taken_from_service_accounts() -> None:
    snap = RosterSnapshot(
        employees=[
            RosterPerson(
                employee_id=A, name="Анна", dismissed=False, service_accounts={"express": H}
            ),
            RosterPerson(
                employee_id=B, name="Борис", dismissed=False, service_accounts={"uis": "7"}
            ),
        ],
        operators=[],
    )
    rows = {r.employee_id: r for r in flatten(snap)}
    assert rows[A].express_huid == H
    assert rows[B].express_huid is None


def test_old_auth_without_service_accounts_still_parses() -> None:
    """Поле аддитивное: старый auth его не отдаёт — HUID просто нет."""
    person = {"employee_id": str(A), "name": "Анна", "dismissed": False}
    snap = RosterSnapshot.model_validate({"operators": [], "employees": [person]})
    (row,) = flatten(snap)
    assert row.express_huid is None
