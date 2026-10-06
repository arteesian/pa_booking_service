"""Раскладка снимка ростера в строки ``directory_employees``. Чистая функция."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from pa_booking.directory.roster import RosterPerson, RosterSnapshot

EXPRESS_SERVICE = "express"


@dataclass(frozen=True)
class EmployeeRow:
    employee_id: uuid.UUID
    full_name: str
    dismissed: bool
    express_huid: str | None = None


def flatten(snapshot: RosterSnapshot) -> tuple[EmployeeRow, ...]:
    """База — ``employees`` (вся директория auth); ``operators`` дополняют тех,
    кого там нет. Оператор встречается по разу на подгруппу — дедуплицируем."""
    rows: dict[uuid.UUID, EmployeeRow] = {e.employee_id: _row(e) for e in snapshot.employees}
    for op in snapshot.operators:
        rows.setdefault(op.employee_id, _row(op))
    return tuple(rows.values())


def _row(person: RosterPerson) -> EmployeeRow:
    return EmployeeRow(
        person.employee_id,
        person.name,
        person.dismissed,
        person.service_accounts.get(EXPRESS_SERVICE),
    )
