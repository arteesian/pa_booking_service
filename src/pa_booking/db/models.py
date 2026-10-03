"""ORM-модели: снимок директории, записи, библиотека."""

from __future__ import annotations

import uuid
from datetime import date, datetime, time
from enum import StrEnum

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from pa_booking.domain.appointments import BookingStatus, Kind


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


def _pg_enum(enum_cls: type[StrEnum], name: str) -> Enum:
    """PostgreSQL ENUM из значений StrEnum (``"psy"``), а не имён членов (``"PSY"``)."""
    return Enum(enum_cls, name=name, values_callable=lambda cls: [m.value for m in cls])


class AppointmentSlot(Base):
    """Слот специалиста: дата и время по МСК. Удаление мягкое — история для выгрузок."""

    __tablename__ = "appointment_slots"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    slot_date: Mapped[date] = mapped_column(nullable=False)
    slot_time: Mapped[time] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        # Одна неудалённая пара (дата, время); после удаления то же время можно добавить снова.
        Index(
            "uq_appointment_slots_active",
            "slot_date",
            "slot_time",
            unique=True,
            postgresql_where=text("removed_at IS NULL"),
        ),
    )


class AppointmentBooking(Base):
    """Бронь слота сотрудником. Отменённые остаются — для «Моих записей» и выгрузок."""

    __tablename__ = "appointment_bookings"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    slot_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("appointment_slots.id"), nullable=False
    )
    employee_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    kind: Mapped[Kind] = mapped_column(_pg_enum(Kind, "appointment_kind"), nullable=False)
    status: Mapped[BookingStatus] = mapped_column(
        _pg_enum(BookingStatus, "appointment_status"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        # Защита от гонки: второй одновременный INSERT активной брони на слот → IntegrityError.
        Index(
            "uq_appointment_bookings_active_slot",
            "slot_id",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
        Index("ix_appointment_bookings_employee", "employee_id", "status"),
    )


class LibraryBook(Base):
    """Книга каталога. Удаление мягкое — история выдач ссылается на книгу."""

    __tablename__ = "library_books"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    genre: Mapped[str] = mapped_column(String(255), nullable=False)
    author: Mapped[str] = mapped_column(String(255), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class LibraryLoan(Base):
    """Выдача книги — она же история (замена ``all_reserv_book`` бота)."""

    __tablename__ = "library_loans"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    book_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("library_books.id"), nullable=False)
    employee_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    starts_on: Mapped[date] = mapped_column(nullable=False)
    due_on: Mapped[date] = mapped_column(nullable=False)
    returned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    returned_by_librarian: Mapped[bool] = mapped_column(
        nullable=False, server_default=text("false")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        # Одна книга — одна невозвращённая выдача: защита от гонки двух броней.
        Index(
            "uq_library_loans_open",
            "book_id",
            unique=True,
            postgresql_where=text("returned_at IS NULL"),
        ),
        Index(
            "ix_library_loans_employee",
            "employee_id",
            postgresql_where=text("returned_at IS NULL"),
        ),
    )
