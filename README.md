# pa_booking_service

Записи к психологу и на консультацию МКР, бронирование книг библиотеки — для
Личного Кабинета Оператора. Дизайн — `docs/superpowers/specs/2026-10-02-booking-service-design.md`.

## Локально

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"

ruff check . && mypy src     # линт и типы (strict)
python -m pytest             # юнит-тесты; db и external по умолчанию пропускаются
```

Настройки — переменные `PA_BOOKING_*` или `.env` в корне (шаблон — `.env.example`).

### Интеграционные тесты (`db`)

Поднимают Postgres через testcontainers, схема строится миграциями Alembic:

```bash
TESTCONTAINERS_RYUK_DISABLED=true python -m pytest -m db -q
```

`TESTCONTAINERS_RYUK_DISABLED` нужен на Docker Desktop: контейнер-уборщик Ryuk
монтирует docker-сокет, а Desktop этот путь не отдаёт. Тестовый контейнер всё равно
убирается за собой (`with PostgresContainer(...)`); Ryuk страхует только аварийное
завершение процесса.

### Тест BotX (`external`)

Шлёт по сообщению `[ТЕСТ ЛК…]` в оба служебных чата eXpress — предупредить
психолога и библиотекаря. Нужны `PA_BOOKING_BOTX_*`, `PA_BOOKING_{APPOINTMENTS,LIBRARY}_*`
в `.env`:

```bash
python -m pytest -m external -q
```

## Выкатка

Схема и порядок — в шапке `docker/docker-compose.yml`. API наружу не публикуется:
его вызывает только BFF по сети `pa_net` (`booking-api:8000`).

## Перенос из ботов

Будущие слоты и брони `psy_bot_v2`, каталог и выдачи `Library_bot` (спека §7, план —
блоки 5 и 6.4). Запуск — откуда есть доступ к MySQL ботов и к Postgres сервиса. В
`.env` добавить `PA_BOOKING_MIGRATE_PSY_MYSQL_URL` и
`PA_BOOKING_MIGRATE_LIBRARY_MYSQL_URL` (`mysql+pymysql://user:pass@host:3306/db`,
пользователь только на чтение).

```bash
pip install -e ".[migrate]"
set -a; . ./.env; set +a
python scripts/import_from_bots.py slots                    # dry-run: план, брони и их тип
python scripts/import_from_bots.py slots --apply            # [--kind <HUID>=<psy|mkr>]
python scripts/import_from_bots.py books [--replace]        # dry-run
python scripts/import_from_bots.py books [--replace] --apply
```

Пользователь брони/выдачи — HUID бота как есть (ростер не нужен; ФИО из ростера
подставляется, если человек там есть). Тип записи — из однозначного комментария
бота («психолог» / «мкр») или `--kind`; без типа `--apply` отказывается. Брони и
книги на руках у Telegram-пользователей (только `tg_id`) не переносятся / переносятся
свободными — печатаются в отчёт. Слоты можно переносить повторно (дубли
пропускаются). Каталог — однократно; в окне переключения — `books --replace`
(перезаливает каталог, отказывается, если в сервисе уже есть выдачи).
