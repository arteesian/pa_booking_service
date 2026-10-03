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
        EmployeeRow(employee_id=A, full_name="Анна", dismissed=False),
        EmployeeRow(employee_id=B, full_name="Борис", dismissed=True),
    }


def test_operator_in_several_subgroups_is_one_row() -> None:
    person = RosterPerson(employee_id=A, name="Анна", dismissed=False)
    assert len(flatten(RosterSnapshot(operators=[person, person]))) == 1
