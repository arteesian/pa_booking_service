# Переключение на два канала: ЛК + eXpress-боты над `pa_booking_service`

Сценарий выкатки по спеке §8 (пересмотр 2026-10-07). Итог: `psy_bot_v2` и
`Library_bot` работают через `booking-api`, их MySQL больше не пишется; разделы
«Записи к психологу / МКР» и «Библиотека» открыты в ЛК всем.

Две части:

- **Подготовка** — до окна, пользователи ничего не замечают: сервис, BFF/SPA, env и
  compose ботов. Старые боты продолжают работать на MySQL.
- **Окно** — короткое (≈ 15–30 минут): боты остановлены, данные переносятся, новые
  боты запускаются, проверка, снятие пилотного гейта в ЛК.

Обозначения: `HUID` — HUID eXpress (UUID); `$SVC` — каталог `pa_booking_service` на
хосте; `/bots` — общий compose ботов на `vm-csat01`.

---

## 0. Что должно быть закоммичено

| Репозиторий | Что |
|---|---|
| `pa_booking_service` | Блок 6: каналы, миграция 0005, `/me`, перенос по HUID, `books --replace` |
| `psy_bot_v2` | Task 7.1: клиент API |
| `Library_bot` | Task 7.2: клиент API, «Выдачи» |
| `pa_bff` | Админка убрана, пункты меню над внешними ссылками; **пилотный гейт ещё на месте** |

---

## Часть 1. Подготовка (до окна)

### 1.1. Ключи

Три разных M2M-ключа (сервис откажется стартовать, если два совпадут):

```bash
openssl rand -hex 32   # ключ бота записей
openssl rand -hex 32   # ключ бота библиотеки
```

Ключ BFF (`PA_BOOKING_API_KEY`) уже есть — не меняем.

### 1.2. Сервис: env

В `$SVC/.env` добавить:

```dotenv
PA_BOOKING_APPOINTMENTS_BOT_API_KEY=<ключ бота записей>
PA_BOOKING_LIBRARY_BOT_API_KEY=<ключ бота библиотеки>
# HUID через запятую; ADMIN_HUIDS из .env старых ботов подходят как есть
PA_BOOKING_APPOINTMENTS_ADMIN_HUIDS=<HUID психолога>
PA_BOOKING_LIBRARY_ADMIN_HUIDS=<HUID библиотекаря>
```

### 1.3. Сервис: код и миграция 0005

```bash
cd $SVC && git pull
docker compose -f docker/docker-compose.yml build
docker compose -f docker/docker-compose.yml --profile tools run --rm migrate   # 0004 → 0005
docker compose -f docker/docker-compose.yml up -d worker beat api
```

Миграция падает со списком id, если у какой-то брони/выдачи нет HUID в ростере
(на 2026-10-07 таблицы пусты — не ожидается). Схема тогда остаётся на 0004.

**Проверка:**

```bash
docker compose -f docker/docker-compose.yml logs --tail=50 api   # без ошибок старта
```

### 1.4. Ростер: у сотрудников КЦ есть HUID

Без привязки eXpress сотрудник КЦ в ЛК видит слоты и каталог, но записаться не
может (409 `express_not_linked`, Д-10). Сколько таких — один запрос к БД сервиса
(после ближайшего синка ростера, он каждые 15 минут):

```sql
SELECT count(*)                                         AS всего,
       count(*) FILTER (WHERE express_huid IS NOT NULL) AS с_huid,
       count(*) FILTER (WHERE express_huid IS NULL AND NOT dismissed) AS без_huid_работают
FROM directory_employees;
```

Если `без_huid_работают` заметно больше нуля — привязать учётки в `pa_auth_service`
до открытия разделов (шаг 2.5), иначе эти люди упрутся в отказ.

### 1.5. Сервис: ключи ботов работают

С хоста, из сети `pa_net` (host-порта у сервиса нет):

```bash
docker run --rm --network pa_net curlimages/curl -s \
  -H "X-API-Key: <ключ бота записей>" -H "X-User-Huid: <HUID психолога>" \
  http://booking-api:8000/appointments/me
# → {"huid":"<HUID психолога>","roles":["psychologist"]}

docker run --rm --network pa_net curlimages/curl -s \
  -H "X-API-Key: <ключ бота библиотеки>" -H "X-User-Huid: <HUID библиотекаря>" \
  http://booking-api:8000/library/me
# → {"huid":"<HUID библиотекаря>","roles":["librarian"]}

docker run --rm --network pa_net curlimages/curl -s -o /dev/null -w "%{http_code}\n" \
  -H "X-API-Key: <ключ бота записей>" -H "X-User-Huid: <HUID психолога>" \
  http://booking-api:8000/library/me
# → 401 (ключ бота записей не открывает библиотеку)
```

### 1.6. BFF и SPA (админка убрана, гейт на месте)

```bash
cd <pa_bff> && git pull
docker compose -f docker/docker-compose.yml build    # образ собирает и SPA
docker compose -f docker/docker-compose.yml up -d api
```

Проверка под admin в ЛК: в меню «Записи к психологу / МКР» и «Библиотека» — над
«График КЦ»; вкладок «Расписание», «Книги», «Выдачи» нет; запись и бронь работают
(если у вашей учётки есть HUID в ростере).

### 1.7. Боты: env и compose (без перезапуска)

В `/bots/psy_bot_v2/.env` и `/bots/Library_bot/.env` **добавить** (старые
переменные `DB_*`, `ADMIN_HUIDS`, `NOTIFY…` можно оставить до окна — новый код их
не читает, старый без них не стартует):

```dotenv
BOOKING_API_URL=http://booking-api:8000
BOOKING_API_KEY=<ключ своего бота>
```

В `/bots/docker-compose.yml` сервисам `psy_bot_v2` и `library_bot`:

```yaml
    networks:
      default: {}   # без этой строки сервис выпадет из общей сети compose
      pa_net: {}
```

и в корень файла:

```yaml
networks:
  pa_net:
    external: true
```

Проверка без перезапуска ботов: `docker compose -f /bots/docker-compose.yml config -q`
(синтаксис). Сеть подключится при пересоздании контейнера в окне.

### 1.8. Перенос: окружение и dry-run

На машине, откуда видны MySQL ботов и Postgres сервиса:

```bash
cd $SVC
pip install -e ".[migrate]"
# в .env: PA_BOOKING_MIGRATE_PSY_MYSQL_URL, PA_BOOKING_MIGRATE_LIBRARY_MYSQL_URL
#         (mysql+pymysql://…, пользователь только на чтение), PA_BOOKING_DATABASE_URL
set -a; . ./.env; set +a
python scripts/import_from_bots.py slots
python scripts/import_from_bots.py books --replace
```

Dry-run ничего не пишет. Посмотреть заранее:

- **слоты:** брони без типа («ТИП НЕ ПОНЯТЕН») — подготовить `--kind <HUID>=psy|mkr`;
  брони Telegram (без HUID) не переносятся — решить с психологом;
- **книги:** «На руках (переносятся выдачами)» — книги с HUID. На 2026-10-06 одна
  такая была просрочена, а библиотекарь говорил, что всё возвращено: после переноса
  она окажется выдачей — библиотекарь закроет её в боте («Выдачи → Отметить
  возврат»). Книги Telegram-пользователей переносятся свободными.

---

## Часть 2. Окно

### 2.1. Остановить старых ботов

```bash
cd /bots && docker compose stop psy_bot_v2 library_bot
```

С этого момента в MySQL никто не пишет.

### 2.2. Перенос

```bash
cd $SVC && set -a; . ./.env; set +a
python scripts/import_from_bots.py slots                      # ещё раз dry-run — цифры финальные
python scripts/import_from_bots.py slots --apply [--kind <HUID>=psy|mkr …]
python scripts/import_from_bots.py books --replace            # dry-run
python scripts/import_from_bots.py books --replace --apply
```

`books --replace` перезаливает каталог (снят до 2026-10-07, библиотекарь мог его
менять) и откажется, если в сервисе уже есть выдачи (тогда — разбираемся, не
форсируем). Слоты переносятся идемпотентно: заведённые в сервисе раньше не
дублируются.

### 2.3. Запустить новых ботов

Обновить код в `/bots/psy_bot_v2` и `/bots/Library_bot` (код смонтирован в `/app`),
затем — сборка обязательна, меняются зависимости:

```bash
cd /bots
docker compose build psy_bot_v2 library_bot
docker compose up -d psy_bot_v2 library_bot
docker compose logs --tail=50 psy_bot_v2 library_bot   # без ошибок старта
```

### 2.4. Проверка вживую

**Бот записей** (под учёткой психолога и под обычной):

- [ ] `/start` → у психолога есть «Администрирование», у обычного — нет;
- [ ] «Записаться» → дата → время → «Психолог» → «✅ Вы успешно записаны»; в
      служебном чате — уведомление (его шлёт сервис);
- [ ] «Мои записи» → запись видна; «Отменить запись» → отменена, уведомление в чате;
- [ ] админ: «Добавить запись» → слоты на будущую дату → «Добавлено слотов: N»;
- [ ] админ: «Удалить запись» → занятый слот → подтверждение с ФИО → у записавшегося
      личное сообщение «…отменена специалистом», в «Моих записях» — пометка;
- [ ] админ: «Записи за месяц» → приходит xlsx;
- [ ] старая кнопка из истории чата → «Эта кнопка устарела — откройте меню заново».

**Бот библиотеки** (под библиотекарем и под обычной учёткой):

- [ ] каталог по жанру → книга → «Бронировать» → дата возврата; уведомление в чате;
- [ ] «Мои бронирования» → книга → «Продлить на 7 дней» → новая дата;
- [ ] «Вернуть книгу» → подтверждение → возвращена;
- [ ] библиотекарь: «Добавить книгу» → строка → `/done` → отчёт о сохранённых;
- [ ] библиотекарь: «Выдачи» → «Просроченные» / «На руках» → «Отметить возврат»;
- [ ] библиотекарь: «История бронирования» → xlsx.

**Два канала** (под учёткой с HUID в ростере, вы — admin, гейт ещё стоит):

- [ ] запись из бота видна в ЛК в «Моих записях», и наоборот;
- [ ] бронь книги из ЛК видна в боте в «Моих бронированиях».

### 2.5. Снять пилотный гейт в ЛК

В `pa_bff/frontend/src`:

- `lib/bookingUi.ts` — `canViewBooking` возвращает `true` (комментарий в функции);
- `lib/shellNavigation.ts` — пункты записей и библиотеки добавлять для всех ролей
  (убрать условие `role === "superadmin"`);
- поправить `test/bookingUi.test.ts` и `test/shell.test.ts` под новое поведение.

Сборка и выкатка — как в 1.6. Проверка: под учёткой оператора разделы в меню есть.

### 2.6. После окна

- Объявить пользователям (бот — как раньше; в ЛК появились разделы для КЦ).
- MySQL ботов — пользователю на чтение (`REVOKE INSERT, UPDATE, DELETE …`) до
  подтверждения переноса; затем вывести. Старые `DB_*`, `ADMIN_HUIDS`,
  `NOTIFY_CHAT_ID` / `NOTIFICATION_CHAT_ID` убрать из `.env` ботов.
- Следить первые дни: метрика `booking_notify_failures_total`, логи
  `booking-api недоступен` в ботах.

---

## Откат

- **Сервис / BFF** до окна — `git checkout <прошлый коммит>`, сборка, выкатка.
  Миграцию 0005 откатывать только если в бронях нет людей не из КЦ
  (`downgrade` упадёт — так задумано, чтобы не потерять их брони).
- **Боты** — откат возможен только **сразу** после окна, пока никто не записался
  через новую систему: остановить, вернуть код и `.env`, `docker compose build`,
  запустить — старые боты продолжат с MySQL. Как только пошли новые записи — они
  есть только в Postgres, откат их потеряет: чиним вперёд.
