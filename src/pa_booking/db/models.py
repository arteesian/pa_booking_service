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
    SmallInteger,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from pa_booking.domain.appointments import BookingStatus, Kind
from pa_booking.domain.identity import Channel


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
    # HUID eXpress (учётка ``express`` в auth) — по нему переносятся брони ботов.
    express_huid: Mapped[str | None] = mapped_column(String(64), index=True)
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
    """Бронь слота. Отменённые остаются — для «Моих записей» и выгрузок.

    Пользователь — HUID eXpress в обоих каналах (спека Р-8); ``user_name`` — снимок
    имени на момент брони: для людей не из ростера другого источника ФИО нет.
    """

    __tablename__ = "appointment_bookings"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    slot_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("appointment_slots.id"), nullable=False
    )
    user_huid: Mapped[uuid.UUID] = mapped_column(nullable=False)
    user_name: Mapped[str | None] = mapped_column(String(256))
    channel: Mapped[Channel] = mapped_column(_pg_enum(Channel, "booking_channel"), nullable=False)
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
        Index("ix_appointment_bookings_user_huid", "user_huid", "status"),
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
    user_huid: Mapped[uuid.UUID] = mapped_column(nullable=False)
    user_name: Mapped[str | None] = mapped_column(String(256))
    channel: Mapped[Channel] = mapped_column(_pg_enum(Channel, "booking_channel"), nullable=False)
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
            "ix_library_loans_user_huid",
            "user_huid",
            postgresql_where=text("returned_at IS NULL"),
        ),
    )


class LibraryRating(Base):
    """Оценка книги: одна на (книга, HUID), повторная — upsert (спека О-2)."""

    __tablename__ = "library_ratings"

    book_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("library_books.id"), primary_key=True
    )
    user_huid: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    user_name: Mapped[str | None] = mapped_column(String(256))
    channel: Mapped[Channel] = mapped_column(_pg_enum(Channel, "booking_channel"), nullable=False)
    score: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (CheckConstraint("score BETWEEN 1 AND 5", name="ck_library_ratings_score"),)


class LibraryComment(Base):
    """Комментарий в обсуждении книги. Удаление мягкое (автор или библиотекарь);
    автор может править текст — ``edited_at`` помечает правку.

    Колонка — ``body``: атрибут ``text`` перекрыл бы ``sqlalchemy.text`` в теле класса.
    """

    __tablename__ = "library_comments"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    book_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("library_books.id"), nullable=False)
    user_huid: Mapped[uuid.UUID] = mapped_column(nullable=False)
    user_name: Mapped[str | None] = mapped_column(String(256))
    channel: Mapped[Channel] = mapped_column(_pg_enum(Channel, "booking_channel"), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    removed_by_librarian: Mapped[bool] = mapped_column(nullable=False, server_default=text("false"))
    edited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index(
            "ix_library_comments_book",
            "book_id",
            "created_at",
            postgresql_where=text("removed_at IS NULL"),
        ),
    )
