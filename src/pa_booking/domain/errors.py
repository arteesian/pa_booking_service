"""Ошибка бизнес-правила. HTTP-статус по ``code`` выбирает слой API."""

from __future__ import annotations


class DomainError(Exception):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail
