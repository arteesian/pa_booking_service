"""Схемы запросов и ответов API записей (спека §5.1)."""

from __future__ import annotations

import uuid
from datetime import date, time

from pydantic import BaseModel, Field

from pa_booking.domain.appointments import BookingStatus, Kind


class SlotOut(BaseModel):
    id: int
    time: time


class DaySlotsOut(BaseModel):
    date: date
    slots: list[SlotOut]


class BookingCreate(BaseModel):
    slot_id: int
    kind: Kind


class BookingOut(BaseModel):
    id: int
    slot_id: int
    date: date
    time: time
    kind: Kind
    status: BookingStatus


class SlotsCreate(BaseModel):
    date: date
    times: list[time] = Field(min_length=1)


class SlotsAddedOut(BaseModel):
    added: list[time]
    duplicates: list[time]


class OverviewBookingOut(BaseModel):
    id: int
    employee_id: uuid.UUID
    full_name: str
    kind: Kind


class OverviewSlotOut(BaseModel):
    id: int
    time: time
    booking: OverviewBookingOut | None


class OverviewDayOut(BaseModel):
    date: date
    slots: list[OverviewSlotOut]
