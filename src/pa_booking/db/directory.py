"""Персистентность снимка директории (ФИО по ``employee_id``).

Снимок заменяется целиком: ростер маленький, а согласованность важнее экономии
на UPDATE. Коммит — у вызывающего.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Sequence
from datetime import datetime

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from pa_booking.db.models import DirectoryEmployee, DirectorySyncState
from pa_booking.directory.sync import EmployeeRow


def save_snapshot(session: Session, rows: Sequence[EmployeeRow], *, now: datetime) -> None:
    """Полностью заменить снимок и отметить время синка."""
    session.execute(delete(DirectoryEmployee))
    session.add_all(
        [
            DirectoryEmployee(
                employee_id=r.employee_id,
                full_name=r.full_name,
                dismissed=r.dismissed,
                express_huid=r.express_huid,
            )
            for r in rows
        ]
    )
    state = session.get(DirectorySyncState, 1)
    if state is None:
        session.add(DirectorySyncState(id=1, synced_at=now, employees_count=len(rows)))
    else:
        state.synced_at = now
        state.employees_count = len(rows)


def names_by_id(session: Session, ids: Iterable[uuid.UUID]) -> dict[uuid.UUID, str]:
    """ФИО по employee_id. Отсутствующие в снимке просто не попадут в результат."""
    wanted = list(ids)
    if not wanted:
        return {}
    rows = session.execute(
        select(DirectoryEmployee.employee_id, DirectoryEmployee.full_name).where(
            DirectoryEmployee.employee_id.in_(wanted)
        )
    ).all()
    return {employee_id: full_name for employee_id, full_name in rows}


def snapshot_age_seconds(session: Session, *, now: datetime) -> float | None:
    """Возраст снимка в секундах; None — синка ещё не было."""
    state = session.get(DirectorySyncState, 1)
    if state is None:
        return None
    return (now - state.synced_at).total_seconds()


def display_names(session: Session, ids: Iterable[uuid.UUID]) -> dict[uuid.UUID, str]:
    """Имя для уведомлений и выгрузок: ФИО из снимка, нет в снимке — employee_id (§5.4)."""
    wanted = set(ids)
    names = names_by_id(session, wanted)
    return {i: names.get(i, str(i)) for i in wanted}


def employees_by_huid(session: Session) -> dict[str, uuid.UUID]:
    """HUID eXpress → employee_id по снимку (у кого учётка ``express`` привязана)."""
    rows = session.execute(
        select(DirectoryEmployee.express_huid, DirectoryEmployee.employee_id).where(
            DirectoryEmployee.express_huid.is_not(None)
        )
    ).all()
    return {huid: employee_id for huid, employee_id in rows if huid}
