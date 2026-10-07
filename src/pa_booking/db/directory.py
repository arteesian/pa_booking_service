"""Персистентность снимка директории: ФИО и HUID eXpress сотрудников ЛК.

Снимок заменяется целиком: ростер маленький, а согласованность важнее экономии
на UPDATE. Коммит — у вызывающего.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
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


def snapshot_age_seconds(session: Session, *, now: datetime) -> float | None:
    """Возраст снимка в секундах; None — синка ещё не было."""
    state = session.get(DirectorySyncState, 1)
    if state is None:
        return None
    return (now - state.synced_at).total_seconds()


def display_names(
    session: Session, people: Iterable[tuple[uuid.UUID, str | None]]
) -> dict[uuid.UUID, str]:
    """Имя для уведомлений и выгрузок по (HUID, снимок имени из брони) — спека §3.1.

    Порядок: ФИО из ростера по HUID (свежее) → снимок → сам HUID. У одного HUID
    в разных бронях снимки могут различаться — берём любой непустой.
    """
    snapshots: dict[uuid.UUID, str | None] = {}
    for huid, snapshot in people:
        if snapshots.get(huid) is None:
            snapshots[huid] = snapshot
    roster = names_by_huid(session, snapshots)
    return {h: roster.get(h) or s or str(h) for h, s in snapshots.items()}


@dataclass(frozen=True)
class LkIdentity:
    """Сотрудник ЛК по снимку: HUID (если учётка eXpress привязана) и ФИО."""

    huid: uuid.UUID | None
    full_name: str


def _as_huid(raw: str | None) -> uuid.UUID | None:
    # Синк кладёт HUID уже нормализованным (directory.sync); разбор — страховка от
    # строк, записанных до нормализации.
    try:
        return None if raw is None else uuid.UUID(raw)
    except ValueError:
        return None


def lk_identity(session: Session, employee_id: uuid.UUID) -> LkIdentity | None:
    """HUID и ФИО сотрудника ЛК; None — сотрудника нет в снимке."""
    row = session.execute(
        select(DirectoryEmployee.express_huid, DirectoryEmployee.full_name).where(
            DirectoryEmployee.employee_id == employee_id
        )
    ).one_or_none()
    return None if row is None else LkIdentity(_as_huid(row[0]), row[1])


def names_by_huid(session: Session, huids: Iterable[uuid.UUID]) -> dict[uuid.UUID, str]:
    """ФИО по HUID из снимка. Нет в снимке (не КЦ, нет привязки) — нет в результате."""
    wanted = {str(h) for h in huids}
    if not wanted:
        return {}
    rows = session.execute(
        select(DirectoryEmployee.express_huid, DirectoryEmployee.full_name).where(
            DirectoryEmployee.express_huid.in_(wanted)
        )
    ).all()
    return {huid: name for raw, name in rows if (huid := _as_huid(raw)) is not None}
