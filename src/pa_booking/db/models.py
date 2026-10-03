"""ORM-модели. Пока только снимок директории; домен — в блоках 2–3."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, String, func, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class DirectoryEmployee(Base):
    """Снимок сотрудника из ростера auth: ФИО по ``employee_id``.

    ФИО нужны в уведомлениях и выгрузках; нет записи — вместо ФИО ``employee_id``
    (спека §5.4).
    """

    __tablename__ = "directory_employees"

    employee_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    full_name: Mapped[str] = mapped_column(String(256), nullable=False)
    dismissed: Mapped[bool] = mapped_column(nullable=False, server_default=text("false"))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class DirectorySyncState(Base):
    """Когда последний раз успешно синкали ростер — вход для /ready."""

    __tablename__ = "directory_sync_state"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=False)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    employees_count: Mapped[int] = mapped_column(nullable=False, server_default=text("0"))

    __table_args__ = (CheckConstraint("id = 1", name="ck_directory_sync_state_singleton"),)
