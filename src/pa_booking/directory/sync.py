"""Раскладка снимка ростера в строки ``directory_employees``. Чистая функция."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

import structlog

from pa_booking.directory.roster import RosterPerson, RosterSnapshot

log = structlog.get_logger(__name__)

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
        normalize_huid(person.service_accounts.get(EXPRESS_SERVICE), person.employee_id),
    )


def normalize_huid(raw: str | None, employee_id: uuid.UUID) -> str | None:
    """HUID в каноническом виде (нижний регистр) — по нему ищут ``names_by_huid`` и
    вход из ЛК. Не UUID — привязки нет (warning): лучше «не привязан», чем промах."""
    if raw is None or not raw.strip():
        return None
    try:
        return str(uuid.UUID(raw.strip()))
    except ValueError:
        log.warning("roster_bad_express_huid", employee_id=str(employee_id), value=raw)
        return None
