"""Раскладка снимка ростера в строки ``directory_employees``. Чистая функция."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from pa_booking.directory.roster import RosterSnapshot


@dataclass(frozen=True)
class EmployeeRow:
    employee_id: uuid.UUID
    full_name: str
    dismissed: bool


def flatten(snapshot: RosterSnapshot) -> tuple[EmployeeRow, ...]:
    """База — ``employees`` (вся директория auth); ``operators`` дополняют тех,
    кого там нет. Оператор встречается по разу на подгруппу — дедуплицируем."""
    rows: dict[uuid.UUID, EmployeeRow] = {
        e.employee_id: EmployeeRow(e.employee_id, e.name, e.dismissed) for e in snapshot.employees
    }
    for op in snapshot.operators:
        rows.setdefault(op.employee_id, EmployeeRow(op.employee_id, op.name, op.dismissed))
    return tuple(rows.values())
