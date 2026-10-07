"""Каналы и модули (спека §3.1, Р-8…Р-11).

Пользователь приходит из одного из двух каналов: ЛК (через BFF) или eXpress-бот.
Ключ пользователя в обоих — HUID eXpress. Модуль нужен, чтобы ключ бота открывал
только ручки своего модуля.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

Module = Literal["appointments", "library"]


class Channel(StrEnum):
    LK = "lk"
    EXPRESS = "express"
