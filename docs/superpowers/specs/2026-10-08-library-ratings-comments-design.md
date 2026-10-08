# Библиотека: оценки и обсуждения книг — дизайн

Дата: 2026-10-08. Статус: реализована (2026-10-08), ждёт прогона интеграционных тестов.
Дополнение к `2026-10-02-booking-service-design.md` (§4.2, §5.2). Фича сквозная:
сервис (`pa_booking_service`) + BFF и SPA (`pa_bff`); бот не меняется.

## 1. Цель и рамки

Читатель ставит книге оценку 1–5 и видит средний балл в каталоге; в карточке книги —
обсуждение: любой может оставить комментарий, автор может его удалить.

**Критерий успеха:** в ЛК у каждой плитки каталога виден средний балл и число
оценок; в карточке книги можно поставить/изменить свою оценку, прочитать
обсуждение, написать комментарий и удалить свой. Библиотекарь может удалить любой
комментарий через API.

**Решения (согласованы 2026-10-08):**

| # | Решение |
|---|---|
| О-1 | Оценивать может **любой** пользователь, без проверки выдач |
| О-2 | Одна оценка на (книга, пользователь); её можно **изменить**, но не снять |
| О-3 | API открыт **обоим каналам** (права как у остальных ручек §5.2); UI — **только в ЛК**, `Library_bot` не меняется |
| О-4 | Комментарий удаляет **автор**; библиотекарь — любой, ручкой `/library/admin/comments/{id}` (кнопка в боте — позже, отдельной задачей) |
| О-5 | ~~Комментарии не редактируются~~ (2026-10-08) → автор **правит** свой комментарий: `PATCH /library/comments/{id}` `{text}`, пометка `edited_at` (миграция `0007`), в `CommentOut` — `edited: bool`; длина 1–2000 символов; подпись — ФИО |
| О-6 | Уведомлений в чат о комментариях и оценках **нет** |
| О-7 | Средний балл считается **на лету** агрегатом (не денормализуем в `library_books`): объёмы — сотни книг, тысячи оценок |

Вне рамок: ответы на комментарии, лайки, пагинация обсуждения, редактирование,
кнопки в боте.

## 2. Модель данных (миграция `0006_library_social`)

```
library_ratings
  book_id       bigint FK → library_books NOT NULL
  user_huid     uuid   NOT NULL
  user_name     varchar(256) NULL           -- снимок, как в library_loans
  channel       enum(lk, express) NOT NULL  -- существующий booking_channel
  score         smallint NOT NULL CHECK (score BETWEEN 1 AND 5)
  created_at    timestamptz NOT NULL
  updated_at    timestamptz NOT NULL
  PRIMARY KEY (book_id, user_huid)

library_comments
  id            bigint PK
  book_id       bigint FK → library_books NOT NULL
  user_huid     uuid   NOT NULL
  user_name     varchar(256) NULL
  channel       enum(lk, express) NOT NULL
  body          text   NOT NULL             -- в API поле `text`; атрибут `text`
                                            -- в модели перекрыл бы sqlalchemy.text
  created_at    timestamptz NOT NULL
  removed_at    timestamptz NULL
  removed_by_librarian boolean NOT NULL DEFAULT false
  INDEX (book_id, created_at) WHERE removed_at IS NULL
```

- Тип `booking_channel` уже есть — миграция его переиспользует
  (`create_type=False`), не создаёт заново.
- Составной PK `(book_id, user_huid)` и есть правило О-2: повторная оценка —
  `INSERT … ON CONFLICT (book_id, user_huid) DO UPDATE SET score, updated_at`
  (PostgreSQL upsert, атомарен без явной блокировки).
- `CHECK` дублирует валидацию Pydantic — последнее слово за БД.
- `downgrade` удаляет обе таблицы.

| Действие | Правило |
|---|---|
| Средний балл | `avg(score)` и `count(*)` по книге; книги без оценок → `avg = null`, `count = 0`. Округление — на клиенте |
| Оценить | Книга жива (`removed_at IS NULL`), иначе 404; upsert |
| Обсуждение | Неудалённые комментарии живой книги, от новых к старым |
| Написать | Книга жива; текст после `strip` 1–2000 символов |
| Удалить свой | Свой и неудалённый → `removed_at = now`; чужой/удалённый → 404 |
| Удалить (библиотекарь) | Любой неудалённый → `removed_at = now`, `removed_by_librarian = true` |
| ФИО автора | `display_names` (ростер → снимок `user_name` → HUID), как в выгрузках |

## 3. API (дополнение к §5.2)

| Метод | Путь | Роль | Описание |
|---|---|---|---|
| GET | `/library/books?genre=` | любой | В `BookOut` **добавлены** `rating_avg: float \| null`, `rating_count: int` — во всех ответах, где есть `BookOut` (каталог, выдачи, админка) |
| GET | `/library/books/{id}/rating` | любой | `RatingOut {avg, count, mine}` |
| PUT | `/library/books/{id}/rating` `{score}` | любой (нужен HUID) | Upsert → `RatingOut` |
| GET | `/library/books/{id}/comments` | любой | `list[CommentOut]` |
| POST | `/library/books/{id}/comments` `{text}` | любой (нужен HUID) | → 201 `CommentOut` |
| DELETE | `/library/comments/{id}` | автор | → 204 |
| DELETE | `/library/admin/comments/{id}` | librarian | → 204 |

```
RatingOut  { avg: float | null, count: int, mine: int | null }
CommentOut { id: int, author_name: str, text: str, created_at: datetime, mine: bool }
RatingIn   { score: int (1..5) }
CommentIn  { text: str (strip, 1..2000) }
```

- Чтение (`GET`) — зависимость `User` (`Principal`, HUID может быть `None`):
  тогда `mine = null` / `false`. Запись — `Me` (`Identified`): без HUID из ЛК →
  409 `express_not_linked`, как у брони.
- Новых кодов ошибок нет: 404 `not_found` (книга удалена/нет, комментарий чужой
  или удалён), 422 — Pydantic.
- **Совместимость с ботом:** `Library_bot/library_client.py::Book` — Pydantic v2
  без `model_config`, по умолчанию `extra="ignore"` → новые поля `BookOut` бот
  игнорирует, правка бота не нужна.

**Код сервиса:**
- `db/models.py` — `LibraryRating`, `LibraryComment`.
- `db/library.py` — `rating_summaries(session, book_ids) -> dict[int, RatingSummary]`
  (один `SELECT book_id, avg, count … GROUP BY` на ответ; книги без оценок в
  словаре отсутствуют → `null/0`). Его зовут **все** ручки, отдающие `BookOut`:
  `_book_out` получает сводку параметром, поэтому поля нигде не «забываются»;
  `catalog` не меняется. Плюс `upsert_rating`, `book_comments`, `lock_comment`.
- Проверка «свой и неудалённый» при удалении — в `api/library.py`, как
  `_own_open_loan` у выдач (404, а `DomainError` по умолчанию дал бы 409).
- `api/library_schemas.py` — `RatingIn/RatingOut/CommentIn/CommentOut`, поля в
  `BookOut`.
- `api/library.py` — ручки выше.

## 4. BFF (`pa_bff`)

`api/library.py` — passthrough через существующий `proxy(..., json=...)`:
`GET/PUT /api/library/books/{id}/rating`, `GET/POST /api/library/books/{id}/comments`,
`DELETE /api/library/comments/{id}`. Админскую ручку BFF не проксирует (админка —
только канал бота, §5.4 → 403). Тела запросов BFF принимает как есть и не
валидирует — валидация в сервисе (тонкий слой).

## 5. SPA (`pa_bff/frontend`)

**Плитка каталога** — высота фиксирована, добавлен подвал:

```
┌──────────────────────────────┐
│ Мастер и Маргарита           │  название, ≤2 строк
│ Михаил Булгаков              │  автор
│ Роман                        │  жанр
│ ★ 4,3 · 7         НА РУКАХ   │  рейтинг слева, бейдж справа
└──────────────────────────────┘
```

Без оценок — приглушённое «Нет оценок». Средний балл — одна цифра после запятой,
запятая как разделитель (`toLocaleString("ru-RU")`).

**Карточка книги** (`BookDialog`, ширина ~40rem): название, автор, жанр; строка
рейтинга «★ 4,3 · 7 оценок» + «Ваша оценка» — пять звёзд-кнопок
(`aria-label="Оценить на N"`, подсветка до наведённой, клик → `PUT`, ответ
обновляет строку); описание со сворачиванием; «Забронировать»/«На руках»; ниже —
блок «Обсуждение (N)» (`BookComments.tsx`): textarea со счётчиком `0/2000` и
«Отправить», лента от новых к старым (ФИО · дата, текст, «Удалить» у своих через
`ConfirmDialog`).

- При открытии карточка параллельно грузит `rating` и `comments`; ошибка одного
  блока показывается в нём, остальное работает.
- После успешной оценки каталог сразу перезапрашивается, чтобы обновилась плитка
  (открытая карточка держит свою копию книги и не мигает).
- Текст комментария — только как текст (`white-space: pre-line`), без HTML.
- Data-слой — `lib/library.ts`: `getRating`, `rateBook`, `listComments`,
  `addComment`, `deleteComment`; типы `Rating`, `Comment`, поля в `Book`.

## 6. Тесты

- **Сервис, unit:** схемы (score 0/6 и пустой/2001-
  символьный текст → ошибка валидации).
- **Сервис, integration (testcontainers):** миграция `0006` вверх/вниз; upsert
  (вторая оценка того же HUID меняет, а не добавляет); `avg/count` в каталоге,
  книга без оценок → `null/0`; оценка/комментарий удалённой книги → 404; чужой
  комментарий → 404, удалённый не виден в ленте; админ-удаление из канала бота
  и 403 из ЛК; `mine` для своего/чужого; без HUID из ЛК → 409 `express_not_linked`.
- **BFF:** проксирование новых ручек (метод, путь, тело, статус как есть).
- **SPA (vitest):** плитка с рейтингом и «Нет оценок»; клик по звезде → `PUT` и
  новый средний; отправка комментария; удаление своего через подтверждение; у
  чужого нет «Удалить».

## 7. Выкатка

1. Сервис: миграция `0006` (`alembic upgrade head`) + новый образ. Обратно
   совместимо: старый BFF и бот новых полей не замечают.
2. BFF + SPA — после сервиса.
