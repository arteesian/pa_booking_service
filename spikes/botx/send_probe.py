"""Спайк BotX: отправка из аккаунта бота без приёма вебхуков (спека §8, шаг 0).

Одноразовый скрипт, не часть сервиса. Секрет не печатается; в лог идут только
метод, путь и статус HTTP-запросов к BotX.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import threading
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID
from zoneinfo import ZoneInfo

import httpx
from loguru import logger
from pybotx import Bot, BotAccountWithSecret

MSK = ZoneInfo("Europe/Moscow")
MODES = ("chats", "sync", "nowait", "wait")
BOTS = ("appointments", "library")  # appointments — аккаунт psy_bot_v2


@dataclass(frozen=True)
class Target:
    bot_id: UUID
    cts_url: str
    secret: str
    chat_id: UUID


def _env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        sys.exit(f"Не задана переменная окружения {name}")
    return value


async def _log_request(request: httpx.Request) -> None:
    print(f"  -> {request.method} {request.url.path}")


async def _log_response(response: httpx.Response) -> None:
    print(f"  <- {response.status_code} {response.request.url.path}")


def _body(label: str) -> str:
    """Тексты из §5.5 спеки: эмодзи и переводы строк — как в бою."""
    stamp = datetime.now(MSK).strftime("%d.%m.%Y %H:%M:%S")
    return (
        f"[ТЕСТ ЛК, спайк BotX: {label}, {stamp} МСК]\n"
        "✅ Тестов Тест Тестович\nЗаписался на 05.10.2026 в 11:00\nПсихолог\n"
        "❌ отмена · ⏳ Дата возврата: 12.10.2026"
    )


async def _send(mode: str, target: Target) -> None:
    client = httpx.AsyncClient(
        timeout=15,
        event_hooks={"request": [_log_request], "response": [_log_response]},
    )
    # cts_url в pybotx типизирован как AnyHttpUrl; Pydantic сам приводит строку
    account = BotAccountWithSecret(
        id=target.bot_id,
        cts_url=target.cts_url,  # type: ignore[arg-type]
        secret_key=target.secret,
    )
    bot = Bot(collectors=[], bot_accounts=[account], httpx_client=client)
    await bot.startup()
    try:
        if mode == "chats":
            # только чтение: в каких чатах состоит бот — так находим chat_id
            chats = await bot.list_chats(bot_id=target.bot_id)
            print(f"  чатов: {len(chats)}")
            for chat in chats:
                print(f"  chat_id={chat.chat_id} type={chat.chat_type.name} name={chat.name!r}")
            return
        body = _body(mode)
        if mode == "sync":
            sync_id = await bot.send_message_sync(
                bot_id=target.bot_id, chat_id=target.chat_id, body=body
            )
        elif mode == "nowait":
            sync_id = await bot.send_message(
                bot_id=target.bot_id, chat_id=target.chat_id, body=body, wait_callback=False
            )
        else:
            sync_id = await bot.send_message(
                bot_id=target.bot_id, chat_id=target.chat_id, body=body, callback_timeout=10
            )
        print(f"  OK sync_id={sync_id}")
    except Exception as exc:  # спайк: фиксируем любой исход как данные
        print(f"  FAIL {type(exc).__name__}: {exc}")
    finally:
        await bot.shutdown()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=MODES)
    parser.add_argument("--bot", choices=BOTS, required=True)
    parser.add_argument(
        "--in-thread",
        action="store_true",
        help="звать asyncio.run из отдельного потока (как из threadpool FastAPI/Celery)",
    )
    args = parser.parse_args()
    # pybotx при импорте глушит свой loguru (logger.disable("pybotx")). Включаем,
    # но только WARNING+: на DEBUG он пишет тела запросов, а без WARNING молча
    # теряется, например, «unsupported chat types skipped»
    logger.remove()
    logger.add(sys.stderr, level="WARNING")
    logger.enable("pybotx")

    # имена — как будущие настройки сервиса (спека §3)
    prefix = f"PA_BOOKING_{args.bot.upper()}"
    target = Target(
        bot_id=UUID(_env(f"{prefix}_BOT_ID")),
        cts_url=_env("PA_BOOKING_BOTX_CTS_URL"),
        secret=_env(f"{prefix}_BOT_SECRET"),
        chat_id=UUID(_env(f"{prefix}_NOTIFY_CHAT_ID")),
    )
    print(f"bot={args.bot} mode={args.mode} bot_id={target.bot_id} chat_id={target.chat_id}")

    def run() -> None:
        asyncio.run(_send(args.mode, target))

    if args.in_thread:
        worker = threading.Thread(target=run)
        worker.start()
        worker.join()
    else:
        run()


if __name__ == "__main__":
    main()
