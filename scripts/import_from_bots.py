"""Перенос из MySQL ботов в pa_booking_service (спека §7, план — блоки 5 и 6.4).

    pip install -e ".[migrate]"
    python scripts/import_from_bots.py slots [--kind <HUID>=<psy|mkr>] [--apply]
    python scripts/import_from_bots.py books [--replace] [--apply]

Без ``--apply`` — dry-run: только план и цифры. Источник читается только SELECT'ами.
Пользователь брони/выдачи — HUID бота как есть; тип записи — из однозначного
комментария бота или ``--kind``. ``books --replace`` — перезалить каталог в окне
переключения (отказ, если в сервисе уже есть выдачи). Подключения — из окружения
(``.env``):

- ``PA_BOOKING_MIGRATE_PSY_MYSQL_URL`` / ``PA_BOOKING_MIGRATE_LIBRARY_MYSQL_URL`` —
  ``mysql+pymysql://user:pass@host:3306/db`` (пользователь только на чтение);
- ``PA_BOOKING_DATABASE_URL`` — Postgres сервиса (как у самого сервиса).
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import UTC, date, datetime

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from pa_booking.core.config import get_settings
from pa_booking.db.session import make_engine_from_settings
from pa_booking.domain.moscow import today_msk
from pa_booking.migrate.bots import (
    BookRow,
    SlotRow,
    apply_books,
    apply_slots,
    parse_kind,
    plan_books,
    plan_slots,
)

PSY_URL = "PA_BOOKING_MIGRATE_PSY_MYSQL_URL"
LIBRARY_URL = "PA_BOOKING_MIGRATE_LIBRARY_MYSQL_URL"


def _source(env_name: str) -> str:
    url = os.environ.get(env_name, "")
    if not url:
        sys.exit(f"Не задана переменная окружения {env_name}")
    return url


def _read(env_name: str, query: str) -> list[tuple[object, ...]]:
    engine = create_engine(_source(env_name))
    try:
        with engine.connect() as conn:
            return [tuple(r) for r in conn.execute(text(query))]
    finally:
        engine.dispose()


def _slots(args: argparse.Namespace, now: datetime) -> None:
    rows = [
        SlotRow(
            date_str=str(d),
            time_str=str(t),
            huid=None if huid is None else str(huid),
            tg_id=None if tg is None else int(str(tg)),
            comment=None if comment is None else str(comment),
        )
        for d, t, huid, tg, comment in _read(
            PSY_URL,
            "SELECT date, time, user_huid, tg_id, comment FROM record_psy_pari ORDER BY id",
        )
    ]
    plan = plan_slots(rows, today=today_msk(now), kinds=dict(parse_kind(k) for k in args.kind))
    print(f"Строк в record_psy_pari: {len(rows)}")
    print(f"Будущих свободных слотов: {len(plan.free)}")
    print(f"Будущих броней с HUID: {len(plan.booked)}")
    for b in plan.booked:
        kind = b.kind or "ТИП НЕ ПОНЯТЕН"
        print(
            f"  {b.slot_date:%d.%m.%Y} {b.slot_time:%H:%M} huid={b.huid}"
            f" комментарий={b.comment!r} → {kind} [{b.source}]"
        )
    if plan.telegram:
        print(f"Будущих броней Telegram (без HUID) — НЕ переносятся: {len(plan.telegram)}")
        for t in plan.telegram:
            print(f"  {t.slot_date:%d.%m.%Y} {t.slot_time:%H:%M} tg_id={t.tg_id}")
    if not args.apply:
        print("Dry-run: ничего не записано. Для записи — --apply.")
        return
    with Session(make_engine_from_settings(get_settings())) as session:
        result = apply_slots(session, plan, now=now)
        session.commit()
    print(f"Записано: слотов {result.added}, уже были {result.skipped}, броней {result.bookings}.")


def _books(args: argparse.Namespace, now: datetime) -> None:
    rows = [
        BookRow(
            str(g or ""),
            str(a or ""),
            str(t or ""),
            str(d or ""),
            huid=None if huid is None else str(huid),
            tg_id=None if tg is None else int(str(tg)),
            start=start if isinstance(start, date) else None,
            end=end if isinstance(end, date) else None,
        )
        for g, a, t, d, huid, tg, start, end in _read(
            LIBRARY_URL,
            "SELECT genre, author, title, description, user_huid, tg_id, start, `end`"
            " FROM Library_books ORDER BY id",
        )
    ]
    plan = plan_books(rows)
    print(f"Книг в Library_books: {len(plan.books)}, жанров: {len({b.genre for b in plan.books})}")
    print(f"На руках (переносятся выдачами): {plan.loans}")
    for b in plan.books:
        if b.loan is not None:
            print(f"  {b.title!r}: huid={b.loan.huid} {b.loan.starts_on} — {b.loan.due_on}")
    if plan.telegram:
        print(f"На руках у Telegram-пользователей — переносятся СВОБОДНЫМИ: {len(plan.telegram)}")
        for title in plan.telegram:
            print(f"  {title!r}")
    if args.replace:
        print("--replace: каталог в сервисе будет удалён и залит заново.")
    if not args.apply:
        print("Dry-run: ничего не записано. Для записи — --apply.")
        return
    with Session(make_engine_from_settings(get_settings())) as session:
        result = apply_books(session, plan, now=now, replace=args.replace)
        session.commit()
    print(f"Записано: книг {result.books}, выдач {result.loans}.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    sub = parser.add_subparsers(dest="what", required=True)
    slots = sub.add_parser("slots", help="будущие слоты записей и брони")
    slots.add_argument("--kind", action="append", default=[], metavar="HUID=KIND")
    slots.add_argument("--apply", action="store_true")
    books = sub.add_parser("books", help="каталог книг и выдачи на руках")
    books.add_argument("--replace", action="store_true", help="перезалить непустой каталог")
    books.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    now = datetime.now(UTC)
    try:
        if args.what == "slots":
            _slots(args, now)
        else:
            _books(args, now)
    except ValueError as exc:
        sys.exit(f"Ошибка: {exc}")


if __name__ == "__main__":
    main()
