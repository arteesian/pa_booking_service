"""Схемы запросов и ответов API библиотеки (спека §5.2)."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Annotated

from pydantic import BaseModel, Field, StringConstraints

Field255 = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)]
Description = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=10_000)
]
COMMENT_MAX = 2000
Score = Annotated[int, Field(ge=1, le=5)]
CommentText = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=COMMENT_MAX)
]


class BookOut(BaseModel):
    id: int
    genre: str
    author: str
    title: str
    description: str
    available: bool
    rating_avg: float | None
    rating_count: int


class LoanOut(BaseModel):
    id: int
    book: BookOut
    starts_on: date
    due_on: date
    overdue: bool
    returned_at: datetime | None


class AdminLoanOut(LoanOut):
    user_huid: uuid.UUID
    full_name: str
    returned_by_librarian: bool


class BookCreate(BaseModel):
    genre: Field255
    author: Field255
    title: Field255
    description: Description


class BookUpdate(BaseModel):
    """Частичное изменение: не переданное (или null) поле не меняется."""

    genre: Field255 | None = None
    author: Field255 | None = None
    title: Field255 | None = None
    description: Description | None = None


class RatingIn(BaseModel):
    score: Score


class RatingOut(BaseModel):
    """Сводка оценок книги; ``mine`` — оценка текущего пользователя (нет HUID — null)."""

    avg: float | None
    count: int
    mine: int | None


class CommentIn(BaseModel):
    text: CommentText


class CommentOut(BaseModel):
    id: int
    author_name: str
    text: str
    created_at: datetime
    mine: bool
