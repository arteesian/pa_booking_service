# pa_booking_service — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** перенести записи (психолог/МКР) и библиотеку из eXpress-ботов в ЛК
отдельным сервисом `pa_booking_service`.

**Architecture:** блоки в порядке §8 спеки: проверка BotX → каркас → записи →
библиотека → BFF+SPA → скрипты переноса. Расписаны блоки 0–1; блоки 2–5 — скоуп,
по шагам расписываем перед началом каждого. Уведомления спрятаны за `Protocol
Notifier`, поэтому исход проверки BotX меняет только `notify/botx.py` и ничего не
блокирует.

**Tech Stack:** Python 3.12+, FastAPI + Pydantic v2, SQLAlchemy 2.0 (sync) + Alembic,
PostgreSQL, Celery + RedBeat, structlog, prometheus-client, httpx + pyjwt (BotX);
pytest, ruff, mypy --strict.

**Spec:** `docs/superpowers/specs/2026-10-02-booking-service-design.md`

## Global Constraints

- Коммитит и пушит **только пользователь**; в конце задачи исполнитель предлагает
  сообщение `prefix(scope): imperative summary` (англ., ≤ ~72 симв.).
- Код, докстринги, документация — на русском.
- `ruff check .` и `mypy src` (strict) чистые после каждой правки — проверяет исполнитель.
- Юнит-тесты по файлу гоняет исполнитель; `db`, `external` и полный прогон — пользователь
  (исполнитель даёт точную команду и ждёт вывод).
- Время — `Europe/Moscow`; даты — `date`/`time`, не строки.
- Ключ пользователя — `employee_id` (UUID из `X-User-Id`).
- Префикс настроек — `PA_BOOKING_`; секреты — `SecretStr`.
- Host-порта нет: только `pa_net`, алиас `booking-api`.
- Модули `appointments` и `library` друг друга не импортируют.
- Уведомление уходит после коммита; сбой BotX операцию не откатывает.
- Ошибки API — `{"detail": "<по-русски>", "code": "<машинный>"}`; чужое — 404.
- Бизнес-правила — как в боте, с расхождениями Д-1…Д-9 (§4.3); иное — только через
  вопрос пользователю.
- Образец — `pa_overtimes_and_charges` (дальше `overtimes`); «копия» в задаче значит:
  файл берётся как есть, с перечисленными заменами.

## Review Focus

1. **Ростер вернул пустой список** (auth недодеплоен, сбой выборки) — синк
   «успешно» затирает все ФИО, уведомления и выгрузки показывают UUID → синк
   отказывается сохранять пустой снимок (Task 1.4).
2. **BotX не настроен** (пустые `PA_BOOKING_*_BOT_*` в dev/тестах) — сервис должен
   подниматься, а уведомления считаться сбоем, а не ронять запись → Task 1.5.
3. **Нелатинский или кривой `X-API-Key`** — `hmac.compare_digest` на `str` с
   не-ASCII бросает `TypeError` → 500 вместо 401 → сравниваем байты (Task 1.2).
4. **`admin` в `X-User-Roles`** не открывает ручки психолога/библиотекаря
   (чувствительные данные) → тест в Task 1.2.
5. **Миграции разошлись с моделями** — интеграционные тесты строят схему через
   `alembic upgrade head`, а не `create_all` (Task 1.3).

## Открытые вопросы

- **BotX `/direct/sync` отвечает `HTTP 204` без тела** (external-тест 2026-10-03,
  13:44 и 13:52 МСК, оба модуля). `BotxNotifier` ждёт JSON `{"status": "ok"}` (контракт
  из pybotx) и считает 204 ошибкой → `tests/external/test_botx.py` красный. Решает факт:
  дошли ли сообщения `[ТЕСТ ЛК, external-тест…]` в служебные чаты.
  Дошли → 204 = успех (как в `express_notif`: только `raise_for_status`), правим `send`
  + юнит-тест; минус — ошибки доставки при 204 не видны. Не дошли → 204 отдаёт не BotX
  (прокси/балансировщик), разбираемся с маршрутом `PA_BOOKING_BOTX_CTS_URL`.
  До решения `spikes/` не удаляем.

---

## Блок 0. Проверка BotX (§8, шаг 0) — одна команда, не блокирует

Вопрос: примет ли BotX сообщение от аккаунта бота, когда сам бот выключен (после
переезда так и будет). Проба уже лежит в `spikes/botx/` (`send_probe.py`, `.env`
заполнен пользователем).

Факты, проверенные на моке (pybotx 0.76.5):

- авторизация v2: токен у CTS не запрашивается, JWT подписывается секретом локально —
  наш процесс не трогает токен живого бота;
- `send_message(wait_callback=False)` → `POST /api/v4/botx/notifications/direct`,
  тело `{"group_chat_id": …, "notification": {"status": "ok", "body": …}}`; так шлёт
  прод-бот `psy_bot_v2`, значит на нашем BotX этот путь рабочий;
- `send_message()` с ожиданием колбэка (как `Library_bot.notify_group`) без приёмника
  падает `CallbackNotReceivedError` по таймауту — так не делаем.

- [ ] **Step 1 (пользователь, когда удобно):** одно сообщение `[ТЕСТ ЛК…]` в чат
  библиотеки:

```bash
cd spikes/botx && docker run --rm --network pa_net -v "$PWD":/spike -w /spike --env-file .env python:3.12-slim sh -c 'pip install -q -r requirements.txt && python send_probe.py nowait --bot library'
```

  Пришло — учётки и сеть в порядке. Та же команда при остановленном `Library_bot`
  (или в день отключения ботов) закрывает вопрос «без бота». Не пришло — ботов не
  отключаем, пока не починим; код сервиса это не задевает.

- [ ] **Step 2:** после того как в блоке 1 появится `external`-тест Notifier, каталог
  `spikes/` удаляем (`.env` из него переносим в корневой `.env`, имена совпадают).

---

## Блок 1. Каркас сервиса

**Итог:** пакет `pa_booking` поднимается, отвечает `/health` `/ready` `/metrics`,
проверяет mesh-заголовки и роли, держит снимок ФИО из ростера auth, умеет слать
уведомление в BotX; Celery-воркер синкает ростер; docker-compose в `pa_net`.
Доменных таблиц и ручек пока нет. `export/xlsx.py` — в блоке 2, где он впервые нужен.

### Task 1.1: pyproject, настройки, логи

**Files:**
- Create: `pyproject.toml`, `src/pa_booking/__init__.py`, `src/pa_booking/core/__init__.py`,
  `src/pa_booking/core/config.py`, `src/pa_booking/core/logging.py`, `.env.example`
- Test: `tests/__init__.py`, `tests/conftest.py`, `tests/unit/__init__.py`, `tests/unit/test_config.py`

**Interfaces:**
- Produces: `Settings` (поля ниже), `get_settings() -> Settings` (lru_cache),
  `configure_logging(*, level: str, debug: bool) -> None`.

- [ ] **Step 1: `pyproject.toml`** (верхние границы — по версиям, на которых живёт
  `overtimes`: SQLAlchemy 2.0.x, redis 7.x)

```toml
[build-system]
requires = ["setuptools>=68", "wheel"]
build-backend = "setuptools.build_meta"

[project]
name = "pa-booking-service"
version = "0.1.0"
description = "Записи к психологу/МКР и библиотека (Личный Кабинет Оператора)"
requires-python = ">=3.12"
license = { text = "Proprietary" }

dependencies = [
    "fastapi>=0.110,<1",
    "uvicorn[standard]>=0.29,<1",
    "pydantic>=2.6,<3",
    "pydantic-settings>=2.2,<3",
    "structlog>=24.1,<27",
    "prometheus-client>=0.20,<1",
    "sqlalchemy>=2.0,<2.1",
    "alembic>=1.13,<2",
    "psycopg[binary]>=3.1,<4",
    "httpx>=0.27,<1",
    "celery>=5.4,<6",
    "celery-redbeat>=2.2,<3",
    "redis>=5.0,<8",
    "pyjwt>=2.8,<3",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0,<10",
    "ruff>=0.4,<1",
    "mypy>=1.10,<3",
    "testcontainers[postgres]>=4.0,<5",
]

[tool.setuptools.packages.find]
where = ["src"]

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-ra -q --strict-markers -m 'not external and not db'"
pythonpath = ["src"]
filterwarnings = ["error::DeprecationWarning"]
markers = [
    "external: tests that hit real external APIs (skipped by default)",
    "db: integration tests requiring Postgres via testcontainers (skipped by default)",
]

[tool.ruff]
line-length = 100
target-version = "py312"
src = ["src", "tests"]

[tool.ruff.lint]
select = ["E", "F", "W", "I", "B", "UP", "SIM", "RUF"]
ignore = ["E501", "RUF001", "RUF002", "RUF003"]

[tool.ruff.lint.per-file-ignores]
"tests/**" = ["B011"]

[tool.mypy]
python_version = "3.12"
strict = true
mypy_path = "src"
packages = ["pa_booking"]

[[tool.mypy.overrides]]
module = ["celery.*", "redbeat.*", "testcontainers.*"]
ignore_missing_imports = true
```

`src/pa_booking/__init__.py`:

```python
"""pa_booking_service: записи к психологу/МКР и библиотека."""

__version__ = "0.1.0"
```

`src/pa_booking/core/__init__.py`, `tests/__init__.py`, `tests/unit/__init__.py` — пустые.

- [ ] **Step 2: venv и зависимости**

```bash
python -m venv .venv && . .venv/bin/activate && pip install -e ".[dev]"
```

- [ ] **Step 3: изоляция настроек в тестах** — `tests/conftest.py`

```python
from __future__ import annotations

from collections.abc import Iterator

import pytest

from pa_booking.core.config import Settings, get_settings


@pytest.fixture(autouse=True)
def _isolate_settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Тесты не зависят от .env и окружения разработчика.

    Закрываем оба источника настроек: переменные ``PA_BOOKING_*`` и файл ``.env``.
    Иначе заведённый по инструкции .env отправит юнит-тесты в настоящую БД/BotX.
    """
    import os

    for name in list(os.environ):
        if name.startswith("PA_BOOKING_"):
            monkeypatch.delenv(name)
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
```

- [ ] **Step 4: падающий тест** — `tests/unit/test_config.py`

```python
from __future__ import annotations

import pytest

from pa_booking.core.config import Settings, get_settings


def test_defaults_mean_not_configured() -> None:
    s = Settings()
    assert s.database_url.get_secret_value() == ""
    assert s.api_key.get_secret_value() == ""
    assert s.botx_cts_url == ""
    assert s.appointments_bot_id == ""
    assert s.library_notify_chat_id == ""


def test_reads_prefixed_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PA_BOOKING_API_KEY", "k")
    monkeypatch.setenv("PA_BOOKING_LIBRARY_BOT_SECRET", "s")
    s = get_settings()
    assert s.api_key.get_secret_value() == "k"
    assert s.library_bot_secret.get_secret_value() == "s"


def test_secrets_hidden_in_repr(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PA_BOOKING_APPOINTMENTS_BOT_SECRET", "top-secret")
    assert "top-secret" not in repr(Settings())
```

Run: `pytest tests/unit/test_config.py -q` → FAIL (`ModuleNotFoundError: pa_booking.core.config`).

- [ ] **Step 5: `src/pa_booking/core/config.py`**

```python
"""Конфигурация сервиса.

Читается из переменных окружения (префикс ``PA_BOOKING_``) или из ``.env``.
Пустое значение означает «не настроено»: сервис поднимается, а зависящая от него
функциональность отражается в ``/ready`` (БД, ростер) или в метрике сбоев
уведомлений (BotX).
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="PA_BOOKING_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    debug: bool = False
    log_level: str = "INFO"

    # DSN Postgres (postgresql+psycopg://user:pass@host:5432/pa_booking).
    database_url: SecretStr = SecretStr("")
    # M2M-ключ mesh: им BFF стучится к нам.
    api_key: SecretStr = SecretStr("")

    # --- pa_auth_service: ростер (ФИО по employee_id) ---
    auth_base_url: str = ""
    auth_api_key: SecretStr = SecretStr("")
    roster_sync_interval_s: int = Field(default=900, ge=60)
    # Возраст снимка, после которого /ready считает сервис деградировавшим.
    roster_max_age_s: int = Field(default=3600, ge=60)

    # --- BotX (спека §3): аккаунты ботов шлют в служебные чаты ---
    botx_cts_url: str = ""
    botx_timeout_s: float = Field(default=15.0, gt=0)
    # Аккаунт psy_bot_v2 → чат специалиста.
    appointments_bot_id: str = ""
    appointments_bot_secret: SecretStr = SecretStr("")
    appointments_notify_chat_id: str = ""
    # Аккаунт Library_bot → чат библиотеки.
    library_bot_id: str = ""
    library_bot_secret: SecretStr = SecretStr("")
    library_notify_chat_id: str = ""

    # --- Celery / RedBeat ---
    celery_broker_url: SecretStr = SecretStr("redis://localhost:6379/0")
    # Порт embedded-экспортёра воркера: job-метрики живут в его процессе.
    worker_metrics_port: int = 9100


@lru_cache
def get_settings() -> Settings:
    return Settings()
```

- [ ] **Step 6: `src/pa_booking/core/logging.py`** — копия `overtimes/core/logging.py`
  без изменений.

- [ ] **Step 7: `.env.example`** (в корне; `.env` в `.gitignore` уже есть)

```bash
PA_BOOKING_DATABASE_URL=postgresql+psycopg://pa_booking:CHANGE_ME@host.docker.internal:5432/pa_booking
PA_BOOKING_API_KEY=
PA_BOOKING_AUTH_BASE_URL=http://auth-api:8000
PA_BOOKING_AUTH_API_KEY=
PA_BOOKING_CELERY_BROKER_URL=redis://redis:6379/0

PA_BOOKING_BOTX_CTS_URL=
PA_BOOKING_APPOINTMENTS_BOT_ID=
PA_BOOKING_APPOINTMENTS_BOT_SECRET=
PA_BOOKING_APPOINTMENTS_NOTIFY_CHAT_ID=
PA_BOOKING_LIBRARY_BOT_ID=
PA_BOOKING_LIBRARY_BOT_SECRET=
PA_BOOKING_LIBRARY_NOTIFY_CHAT_ID=
```

`PA_BOOKING_AUTH_BASE_URL` — сверить с `.env` `overtimes` (тот же auth в `pa_net`).

- [ ] **Step 8:** `pytest tests/unit/test_config.py -q` → PASS; `ruff check . && mypy src` чистые.

- [ ] **Step 9: коммит** — `chore(core): scaffold package, settings and logging`

### Task 1.2: роли, mesh-auth, формат ошибок

**Files:**
- Create: `src/pa_booking/domain/__init__.py`, `src/pa_booking/domain/roles.py`,
  `src/pa_booking/core/errors.py`, `src/pa_booking/core/auth.py`
- Test: `tests/unit/test_roles.py`, `tests/unit/test_mesh_auth.py`, `tests/unit/test_errors.py`

**Interfaces:**
- Consumes: `Settings.api_key`, `get_settings`.
- Produces: `Role(StrEnum)` {`PSYCHOLOGIST`, `LIBRARIAN`}; `parse_roles(raw: str | None)
  -> frozenset[Role]`; `ApiError(status_code: int, code: str, detail: str)`;
  `install_error_handlers(app: FastAPI) -> None`; `MeshPrincipal(employee_id: UUID,
  roles: frozenset[Role])`; `get_current_user(...) -> MeshPrincipal`;
  `require_role(role: Role) -> Callable[..., MeshPrincipal]`.

Решение: невалидный UUID в `X-User-Id` → 401 (спека §5.4: «нет `X-User-Id`» — 401;
у `overtimes` тут 400, но для BFF это одна и та же поломка контекста).

- [ ] **Step 1: падающие тесты**

`tests/unit/test_roles.py`:

```python
from __future__ import annotations

from pa_booking.domain.roles import Role, parse_roles


def test_parses_csv_and_drops_unknown() -> None:
    assert parse_roles("psychologist, operator ,librarian") == {
        Role.PSYCHOLOGIST,
        Role.LIBRARIAN,
    }


def test_empty_header_means_no_roles() -> None:
    assert parse_roles(None) == frozenset()
    assert parse_roles("") == frozenset()


def test_admin_is_not_our_role() -> None:
    """admin другие роли неявно не покрывает (CLAUDE.md): для нас его нет."""
    assert parse_roles("admin") == frozenset()
```

`tests/unit/test_mesh_auth.py`:

```python
from __future__ import annotations

import uuid
from typing import Annotated

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from pa_booking.core.auth import MeshPrincipal, get_current_user, require_role
from pa_booking.core.config import Settings, get_settings
from pa_booking.core.errors import install_error_handlers
from pa_booking.domain.roles import Role

API_KEY = "test-mesh-key"


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    install_error_handlers(app)

    @app.get("/who")
    def who(p: Annotated[MeshPrincipal, Depends(get_current_user)]) -> dict[str, object]:
        return {"id": str(p.employee_id), "roles": sorted(p.roles)}

    @app.get("/psy")
    def psy(
        p: Annotated[MeshPrincipal, Depends(require_role(Role.PSYCHOLOGIST))],
    ) -> dict[str, bool]:
        return {"ok": True}

    app.dependency_overrides[get_settings] = lambda: Settings(_env_file=None, api_key=API_KEY)
    return TestClient(app)


def _headers(*, key: str = API_KEY, user: str | None = None, roles: str = "") -> dict[str, str]:
    return {"X-API-Key": key, "X-User-Id": user or str(uuid.uuid4()), "X-User-Roles": roles}


def test_missing_api_key_is_401_with_code(client: TestClient) -> None:
    r = client.get("/who", headers={"X-User-Id": str(uuid.uuid4())})
    assert r.status_code == 401
    assert r.json()["code"] == "unauthorized"
    assert isinstance(r.json()["detail"], str)


def test_wrong_api_key_is_401(client: TestClient) -> None:
    assert client.get("/who", headers=_headers(key="nope")).status_code == 401


def test_non_ascii_api_key_is_401_not_500(client: TestClient) -> None:
    # Байтами: httpx не отправит str с не-ASCII, а по сети такие байты прийти могут.
    r = client.get(
        "/who",
        headers={"X-API-Key": "ключ".encode(), "X-User-Id": str(uuid.uuid4()).encode()},
    )
    assert r.status_code == 401


def test_missing_user_id_is_401(client: TestClient) -> None:
    assert client.get("/who", headers={"X-API-Key": API_KEY}).status_code == 401


def test_non_uuid_user_id_is_401(client: TestClient) -> None:
    assert client.get("/who", headers=_headers(user="not-a-uuid")).status_code == 401


def test_valid_headers_give_principal(client: TestClient) -> None:
    user = str(uuid.uuid4())
    r = client.get("/who", headers=_headers(user=user, roles="librarian,operator"))
    assert r.status_code == 200
    assert r.json() == {"id": user, "roles": ["librarian"]}


def test_role_gate_rejects_without_role(client: TestClient) -> None:
    r = client.get("/psy", headers=_headers(roles="librarian"))
    assert r.status_code == 403
    assert r.json()["code"] == "forbidden"


def test_admin_does_not_open_psychologist_routes(client: TestClient) -> None:
    assert client.get("/psy", headers=_headers(roles="admin")).status_code == 403


def test_role_gate_accepts_role(client: TestClient) -> None:
    assert client.get("/psy", headers=_headers(roles="psychologist")).status_code == 200
```

`tests/unit/test_errors.py`:

```python
from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

from pa_booking.core.errors import ApiError, install_error_handlers


class _Body(BaseModel):
    slot_id: int


def _client() -> TestClient:
    app = FastAPI()
    install_error_handlers(app)

    @app.post("/thing")
    def thing(body: _Body) -> dict[str, int]:
        return {"slot_id": body.slot_id}

    @app.get("/busy")
    def busy() -> None:
        raise ApiError(409, "slot_unavailable", "Слот уже занят")

    return TestClient(app)


def test_api_error_shape() -> None:
    r = _client().get("/busy")
    assert r.status_code == 409
    assert r.json() == {"detail": "Слот уже занят", "code": "slot_unavailable"}


def test_validation_error_shape() -> None:
    r = _client().post("/thing", json={"slot_id": "x"})
    assert r.status_code == 422
    assert r.json()["code"] == "validation_error"
    assert "slot_id" in r.json()["detail"]
    assert set(r.json()) == {"detail", "code"}


def test_unknown_route_shape() -> None:
    r = _client().get("/nope")
    assert r.status_code == 404
    assert r.json() == {"detail": "Не найдено", "code": "not_found"}
```

Run: `pytest tests/unit/test_roles.py tests/unit/test_mesh_auth.py tests/unit/test_errors.py -q` → FAIL (нет модулей).

- [ ] **Step 2: `src/pa_booking/domain/roles.py`** (`domain/__init__.py` — пустой)

```python
"""Роли сервиса (спека §3, Д-5).

Роли — множество без иерархии. ``admin`` в наш набор не входит и поэтому ничего
здесь не открывает: записи к психологу — чувствительные данные, их видит только
``psychologist`` (CLAUDE.md). Коды ролей — строковый контракт с ``pa_auth_service``.
"""

from __future__ import annotations

from enum import StrEnum


class Role(StrEnum):
    PSYCHOLOGIST = "psychologist"
    LIBRARIAN = "librarian"


_BY_VALUE: dict[str, Role] = {r.value: r for r in Role}


def parse_roles(raw: str | None) -> frozenset[Role]:
    """CSV из ``X-User-Roles`` → наши роли; чужие роли отбрасываются молча."""
    if not raw:
        return frozenset()
    found = (_BY_VALUE.get(part.strip()) for part in raw.split(","))
    return frozenset(role for role in found if role is not None)
```

- [ ] **Step 3: `src/pa_booking/core/errors.py`**

```python
"""Единый формат ошибок API (спека §5.4): ``{"detail": "<по-русски>", "code": "<машинный>"}``.

SPA ветвится по ``code``, человеку показывает ``detail``. Сюда же сводятся ошибки
самого FastAPI (404 неизвестного пути, 422 Pydantic), чтобы формат был один.
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

_HTTP_DEFAULTS: dict[int, tuple[str, str]] = {
    401: ("unauthorized", "Требуется аутентификация"),
    403: ("forbidden", "Недостаточно прав"),
    404: ("not_found", "Не найдено"),
    405: ("method_not_allowed", "Метод не поддерживается"),
}


class ApiError(Exception):
    """Ожидаемая ошибка API с машинным кодом."""

    def __init__(self, status_code: int, code: str, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.code = code
        self.detail = detail


def _body(code: str, detail: str) -> dict[str, str]:
    return {"detail": detail, "code": code}


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(_request: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=_body(exc.code, exc.detail))

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code, detail = _HTTP_DEFAULTS.get(
            exc.status_code, (f"http_{exc.status_code}", str(exc.detail))
        )
        return JSONResponse(
            status_code=exc.status_code, content=_body(code, detail), headers=exc.headers
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_request: Request, exc: RequestValidationError) -> JSONResponse:
        errors = exc.errors()
        first = errors[0] if errors else {}
        where = ".".join(str(part) for part in first.get("loc", ()))
        detail = f"Некорректный запрос: {where} — {first.get('msg', '')}"
        return JSONResponse(status_code=422, content=_body("validation_error", detail))
```

- [ ] **Step 4: `src/pa_booking/core/auth.py`**

```python
"""Меш-аутентификация M2M (CLAUDE.md, «Архитектурные рамки»).

BFF уже провалидировал пользователя по JWKS и шлёт M2M-ключ ``X-API-Key`` плюс
проброшенный контекст ``X-User-Id`` / ``X-User-Roles``. JWT здесь не валидируется.
Паттерн — ``overtimes/core/auth.py``.
"""

from __future__ import annotations

import hmac
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Header

from pa_booking.core.config import Settings, get_settings
from pa_booking.core.errors import ApiError
from pa_booking.domain.roles import Role, parse_roles


@dataclass(frozen=True)
class MeshPrincipal:
    """Пользователь, от имени которого BFF выполняет запрос."""

    employee_id: uuid.UUID
    roles: frozenset[Role]


def get_current_user(
    settings: Annotated[Settings, Depends(get_settings)],
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
    x_user_id: Annotated[str | None, Header(alias="X-User-Id")] = None,
    x_user_roles: Annotated[str | None, Header(alias="X-User-Roles")] = None,
) -> MeshPrincipal:
    """Проверить M2M-ключ и извлечь принципала из проброшенного контекста."""
    expected = settings.api_key.get_secret_value()
    # Байты, а не str: compare_digest на str с не-ASCII бросает TypeError → 500.
    if (
        not expected
        or not x_api_key
        or not hmac.compare_digest(x_api_key.encode(), expected.encode())
    ):
        raise ApiError(401, "unauthorized", "Нет или неверный X-API-Key")
    try:
        employee_id = uuid.UUID(x_user_id or "")
    except ValueError as exc:
        raise ApiError(401, "unauthorized", "Нет или неверный X-User-Id") from exc
    return MeshPrincipal(employee_id=employee_id, roles=parse_roles(x_user_roles))


def require_role(role: Role) -> Callable[..., MeshPrincipal]:
    """Зависимость FastAPI: пропустить только принципала с ролью ``role``."""

    def _dependency(
        principal: Annotated[MeshPrincipal, Depends(get_current_user)],
    ) -> MeshPrincipal:
        if role not in principal.roles:
            raise ApiError(403, "forbidden", "Недостаточно прав")
        return principal

    return _dependency
```

- [ ] **Step 5:** тесты Step 1 → PASS; `ruff check . && mypy src` чистые.

- [ ] **Step 6: коммит** — `feat(core): add mesh auth, roles and error format`

### Task 1.3: БД — модели директории, сессия, Alembic

**Files:**
- Create: `src/pa_booking/db/__init__.py`, `src/pa_booking/db/models.py`,
  `src/pa_booking/db/session.py`, `alembic.ini`, `alembic/env.py`,
  `alembic/script.py.mako`, `alembic/versions/0001_directory.py`
- Test: `tests/integration/__init__.py`, `tests/integration/conftest.py`,
  `tests/integration/test_migrations.py`

**Interfaces:**
- Produces: `Base`; `DirectoryEmployee(employee_id: UUID PK, full_name: str,
  dismissed: bool, updated_at)`; `DirectorySyncState(id=1, synced_at, employees_count)`;
  `make_engine_from_settings(settings) -> Engine`; `make_sessionmaker(engine)`;
  фикстуры `pg_engine` (схема через `alembic upgrade head`), `db_session`.

- [ ] **Step 1: `src/pa_booking/db/models.py`** (`db/__init__.py` — пустой)

```python
"""ORM-модели. Пока только снимок директории; домен — в блоках 2–3."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, String, func, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


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
```

- [ ] **Step 2: `src/pa_booking/db/session.py`** — копия `overtimes/db/session.py`;
  замены: `pa_overtimes` → `pa_booking`, `PA_OVERTIMES_DATABASE_URL` →
  `PA_BOOKING_DATABASE_URL`.

- [ ] **Step 3: Alembic.** `alembic.ini` и `alembic/script.py.mako` — копии из
  `overtimes`. `alembic/env.py` — копия с заменами `pa_overtimes` → `pa_booking`,
  `PA_OVERTIMES_` → `PA_BOOKING_` и одной правкой `_dsn()`: URL, заданный явно в
  конфиге Alembic (так делают интеграционные тесты), важнее настроек:

```python
def _dsn() -> str:
    explicit = config.get_main_option("sqlalchemy.url")
    if explicit:
        return explicit
    dsn = get_settings().database_url.get_secret_value()
    if not dsn:
        raise RuntimeError("PA_BOOKING_DATABASE_URL is not set")
    return dsn
```

`alembic/versions/0001_directory.py`:

```python
"""Снимок директории: ФИО сотрудников и состояние синка.

Revision ID: 0001_directory
Revises:
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0001_directory"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "directory_employees",
        sa.Column("employee_id", sa.Uuid(), primary_key=True),
        sa.Column("full_name", sa.String(256), nullable=False),
        sa.Column("dismissed", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_table(
        "directory_sync_state",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=False),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("employees_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.CheckConstraint("id = 1", name="ck_directory_sync_state_singleton"),
    )


def downgrade() -> None:
    op.drop_table("directory_sync_state")
    op.drop_table("directory_employees")
```

- [ ] **Step 4: интеграционные фикстуры** — `tests/integration/conftest.py`
  (`tests/integration/__init__.py` — пустой)

```python
from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session

from alembic import command
from alembic.config import Config
from pa_booking.db.models import Base

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="session")
def pg_engine() -> Iterator[Engine]:
    """Postgres в testcontainers; схема — через миграции, а не create_all.

    Так тесты ловят расхождение миграций с моделями.
    """
    from testcontainers.community.postgres import PostgresContainer

    with PostgresContainer("postgres:16-alpine", driver="psycopg") as container:
        url = container.get_connection_url()
        cfg = Config(str(ROOT / "alembic.ini"))
        cfg.set_main_option("script_location", str(ROOT / "alembic"))
        cfg.set_main_option("sqlalchemy.url", url)
        command.upgrade(cfg, "head")
        engine = create_engine(url, pool_pre_ping=True)
        try:
            yield engine
        finally:
            engine.dispose()


@pytest.fixture
def db_session(pg_engine: Engine) -> Iterator[Session]:
    """Сессия на тест; после теста таблицы чистятся — тесты не видят друг друга."""
    with Session(pg_engine) as session:
        yield session
        session.rollback()
        for table in reversed(Base.metadata.sorted_tables):
            session.execute(table.delete())
        session.commit()
```

`tests/integration/test_migrations.py`:

```python
from __future__ import annotations

import pytest
from sqlalchemy import Engine, inspect

from pa_booking.db.models import Base

pytestmark = pytest.mark.db


def test_migrations_create_every_model_table(pg_engine: Engine) -> None:
    tables = set(inspect(pg_engine).get_table_names())
    assert set(Base.metadata.tables) <= tables


def test_migration_columns_match_models(pg_engine: Engine) -> None:
    insp = inspect(pg_engine)
    for name, table in Base.metadata.tables.items():
        db_cols = {c["name"] for c in insp.get_columns(name)}
        assert db_cols == {c.name for c in table.columns}, name
```

- [ ] **Step 5:** `ruff check . && mypy src` чистые. **Пользователь:**
  `pytest -m db tests/integration/test_migrations.py -q` → ждём вывод.

- [ ] **Step 6: коммит** — `feat(db): add directory tables and alembic baseline`

### Task 1.4: ростер — клиент, раскладка, репозиторий, синк

**Files:**
- Create: `src/pa_booking/directory/__init__.py`, `src/pa_booking/directory/roster.py`,
  `src/pa_booking/directory/sync.py`, `src/pa_booking/db/directory.py`,
  `src/pa_booking/core/metrics.py`
- Test: `tests/unit/test_roster_client.py`, `tests/unit/test_roster_flatten.py`,
  `tests/integration/test_directory_repo.py`

**Interfaces:**
- Produces: `fetch_roster_snapshot(client: HttpClient, *, api_key: str) -> RosterSnapshot`,
  `DirectoryFetchError`, `make_httpx_client(base_url) -> HttpClient`;
  `EmployeeRow(employee_id, full_name, dismissed)`, `flatten(snapshot) ->
  tuple[EmployeeRow, ...]`; `save_snapshot(session, rows, *, now)`,
  `names_by_id(session, ids) -> dict[UUID, str]`, `snapshot_age_seconds(session, *, now)
  -> float | None`; метрики `registry`, `roster_sync_*`, `roster_employees`,
  `notify_failures_total{module}`.

- [ ] **Step 1: падающие тесты**

`tests/unit/test_roster_client.py`:

```python
from __future__ import annotations

import json
import uuid

import pytest

from pa_booking.directory.roster import ROSTER_PATH, DirectoryFetchError, fetch_roster_snapshot

EMP = str(uuid.uuid4())


class _Client:
    def __init__(self, status: int = 200, body: bytes = b"", exc: Exception | None = None):
        self.status, self.body, self.exc = status, body, exc
        self.calls: list[tuple[str, dict[str, str]]] = []

    def get(self, path: str, *, headers: dict[str, str]) -> tuple[int, bytes]:
        self.calls.append((path, headers))
        if self.exc:
            raise self.exc
        return self.status, self.body


def _body(**extra: object) -> bytes:
    payload: dict[str, object] = {
        "reference_groups": {},
        "operators": [{"employee_id": EMP, "name": "Иванов И.", "subgroup": "g", "dismissed": False}],
        "employees": [],
    }
    payload.update(extra)
    return json.dumps(payload).encode()


def test_parses_snapshot_and_ignores_extra_fields() -> None:
    snap = fetch_roster_snapshot(_Client(body=_body(aliases={})), api_key="k")
    assert str(snap.operators[0].employee_id) == EMP


def test_sends_api_key_to_roster_path() -> None:
    client = _Client(body=_body())
    fetch_roster_snapshot(client, api_key="k")
    assert client.calls == [(ROSTER_PATH, {"X-API-Key": "k"})]


def test_non_200_raises() -> None:
    with pytest.raises(DirectoryFetchError):
        fetch_roster_snapshot(_Client(status=503, body=b"down"), api_key="k")


def test_missing_operators_field_raises() -> None:
    """Схема без operators — это не «пустой ростер», а поломка контракта."""
    with pytest.raises(DirectoryFetchError):
        fetch_roster_snapshot(_Client(body=b'{"employees": []}'), api_key="k")


def test_network_failure_raises_domain_error() -> None:
    with pytest.raises(DirectoryFetchError):
        fetch_roster_snapshot(_Client(exc=OSError("refused")), api_key="k")
```

`tests/unit/test_roster_flatten.py`:

```python
from __future__ import annotations

import uuid

from pa_booking.directory.roster import RosterPerson, RosterSnapshot
from pa_booking.directory.sync import EmployeeRow, flatten

A, B = uuid.uuid4(), uuid.uuid4()


def test_employees_are_the_base_and_operators_fill_gaps() -> None:
    snap = RosterSnapshot(
        employees=[RosterPerson(employee_id=A, name="Анна", dismissed=False)],
        operators=[
            RosterPerson(employee_id=A, name="Анна (подгруппа)", dismissed=False),
            RosterPerson(employee_id=B, name="Борис", dismissed=True),
        ],
    )
    assert set(flatten(snap)) == {
        EmployeeRow(employee_id=A, full_name="Анна", dismissed=False),
        EmployeeRow(employee_id=B, full_name="Борис", dismissed=True),
    }


def test_operator_in_several_subgroups_is_one_row() -> None:
    person = RosterPerson(employee_id=A, name="Анна", dismissed=False)
    assert len(flatten(RosterSnapshot(operators=[person, person]))) == 1
```

`tests/integration/test_directory_repo.py`:

```python
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from pa_booking.db.directory import names_by_id, save_snapshot, snapshot_age_seconds
from pa_booking.directory.sync import EmployeeRow

pytestmark = pytest.mark.db
NOW = datetime(2026, 10, 5, 9, 0, tzinfo=UTC)


def test_snapshot_replaces_previous(db_session: Session) -> None:
    a, b = uuid.uuid4(), uuid.uuid4()
    save_snapshot(db_session, (EmployeeRow(a, "Анна", False),), now=NOW)
    db_session.commit()
    save_snapshot(db_session, (EmployeeRow(b, "Борис", False),), now=NOW)
    db_session.commit()
    assert names_by_id(db_session, [a, b]) == {b: "Борис"}


def test_snapshot_age(db_session: Session) -> None:
    assert snapshot_age_seconds(db_session, now=NOW) is None
    save_snapshot(db_session, (EmployeeRow(uuid.uuid4(), "Анна", False),), now=NOW)
    db_session.commit()
    assert snapshot_age_seconds(db_session, now=NOW + timedelta(minutes=5)) == 300
```

Run: `pytest tests/unit/test_roster_client.py tests/unit/test_roster_flatten.py -q` → FAIL.

- [ ] **Step 2: `src/pa_booking/directory/roster.py`** — копия `overtimes/directory/roster.py`
  (`directory/__init__.py` — пустой); заменить модели снимка: нам нужны только ФИО.

```python
class RosterPerson(BaseModel):
    """Сотрудник из ростера. Лишние поля (subgroup, aliases…) Pydantic игнорирует."""

    employee_id: uuid.UUID
    name: str
    dismissed: bool


class RosterSnapshot(BaseModel):
    # operators обязателен: его отсутствие — поломка контракта, а не пустой ростер.
    operators: list[RosterPerson]
    # Дефолт пустой — старый auth без этого поля (как в overtimes).
    employees: list[RosterPerson] = []
```

Классы `RosterOperator`, `RosterEmployee`, `RosterReferenceGroup` не переносим; модульный
докстринг — про ФИО вместо линий.

- [ ] **Step 3: `src/pa_booking/directory/sync.py`**

```python
"""Раскладка снимка ростера в строки ``directory_employees``. Чистая функция."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from pa_booking.directory.roster import RosterSnapshot


@dataclass(frozen=True)
class EmployeeRow:
    employee_id: uuid.UUID
    full_name: str
    dismissed: bool


def flatten(snapshot: RosterSnapshot) -> tuple[EmployeeRow, ...]:
    """База — ``employees`` (вся директория auth); ``operators`` дополняют тех,
    кого там нет. Оператор встречается по разу на подгруппу — дедуплицируем."""
    rows: dict[uuid.UUID, EmployeeRow] = {
        e.employee_id: EmployeeRow(e.employee_id, e.name, e.dismissed)
        for e in snapshot.employees
    }
    for op in snapshot.operators:
        rows.setdefault(op.employee_id, EmployeeRow(op.employee_id, op.name, op.dismissed))
    return tuple(rows.values())
```

- [ ] **Step 4: `src/pa_booking/db/directory.py`** — из `overtimes/db/directory.py` берём
  `names_by_id` и `snapshot_age_seconds` как есть; `list_lines`/`line_titles` не
  переносим; `save_snapshot` — без членств и линий:

```python
def save_snapshot(session: Session, rows: Sequence[EmployeeRow], *, now: datetime) -> None:
    """Полностью заменить снимок и отметить время синка (одна транзакция у вызывающего)."""
    session.execute(delete(DirectoryEmployee))
    session.add_all(
        [
            DirectoryEmployee(
                employee_id=r.employee_id, full_name=r.full_name, dismissed=r.dismissed
            )
            for r in rows
        ]
    )
    state = session.get(DirectorySyncState, 1)
    if state is None:
        session.add(DirectorySyncState(id=1, synced_at=now, employees_count=len(rows)))
    else:
        state.synced_at = now
        state.employees_count = len(rows)
```

- [ ] **Step 5: `src/pa_booking/core/metrics.py`** — копия `overtimes/core/metrics.py`:
  реестр и коллекторы процесса как есть; префикс `overtimes_` → `booking_`;
  `roster_operators` → `roster_employees` (`booking_roster_employees`); доменные
  `bookings_total`/`charges_total` убрать; добавить:

```python
# --- Уведомления BotX (спека §5.4): сбой не откатывает операцию, но виден здесь ---

notify_failures_total = Counter(
    "booking_notify_failures_total",
    "Неотправленные уведомления в служебные чаты eXpress.",
    labelnames=("module",),
    registry=registry,
)
```

- [ ] **Step 6:** юнит-тесты Step 1 → PASS; `ruff check . && mypy src` чистые.
  **Пользователь:** `pytest -m db tests/integration/test_directory_repo.py -q`.

- [ ] **Step 7: коммит** — `feat(directory): sync employee names from auth roster`

### Task 1.5: уведомления — Notifier, BotX, Fake

**Files:**
- Create: `src/pa_booking/notify/__init__.py`, `src/pa_booking/notify/botx.py`
- Test: `tests/unit/test_notify.py`, `tests/external/__init__.py`, `tests/external/test_botx.py`

**Interfaces:**
- Consumes: `Settings.botx_*`, `Settings.{appointments,library}_*`, `notify_failures_total`.
- Produces: `Module = Literal["appointments", "library"]`; `Notifier` (Protocol:
  `send(text: str) -> None`, бросает при сбое); `BotxSendError`; `BotxTarget`;
  `botx_target(settings, module) -> BotxTarget | None`; `build_botx_jwt(*, bot_id,
  host, secret, now) -> str`; `BotxNotifier(target, *, timeout_s, transport=None)`;
  `FakeNotifier(*, fail: bool = False)` с `.sent: list[str]`;
  `make_notifier(settings, module) -> Notifier | None`;
  `notify_safely(notifier, text, *, module) -> None` (никогда не бросает).

**Как устроено (решение 2026-10-03, вариант «Б»).** Без pybotx: синхронный
`httpx.post` на `POST {cts_url}/api/v4/botx/notifications/direct/sync` с заголовком
`Authorization: Bearer <JWT>`. Так же шлёт сервис `express_notif`
(`projects/express_notif/express_usedesk_proxy.py`) — значит, на нашем BotX этот путь
рабочий. JWT — как `pybotx.auth.build_botx_jwt_v2` (сверено построчно): HS256 секретом
бота, `iss` = bot_id, `aud` = hostname из `cts_url`, `exp` = `iat` + 60, `nbf`, `jti`,
`version: 2`. Синхронный endpoint отвечает результатом сразу — колбэков нет, ошибки
видны. Ответ проверяем дважды: HTTP-статус **и** `status` в теле — pybotx
(`DirectNotificationSyncMethod`) показывает, что ошибка доставки приходит как
`{"status": "error", "reason": …}`.

Как это будет использоваться в блоках 2–3: ручка коммитит транзакцию и вешает
`background_tasks.add_task(notify_safely, notifier, text, module=...)` — отправка идёт
после ответа клиенту, медленный BotX запрос не задерживает.

- [ ] **Step 1: зависимость** — `pyjwt` уже в `pyproject.toml` (Task 1.1, согласовано
  пользователем); pybotx сервису не нужен.

- [ ] **Step 2: падающие тесты** — `tests/unit/test_notify.py`

```python
from __future__ import annotations

import json
import uuid

import httpx
import jwt
import pytest
from pydantic import SecretStr

from pa_booking.core.config import Settings
from pa_booking.core.metrics import notify_failures_total
from pa_booking.notify.botx import (
    BotxNotifier,
    BotxSendError,
    BotxTarget,
    FakeNotifier,
    botx_target,
    build_botx_jwt,
    make_notifier,
    notify_safely,
)

BOT, CHAT = uuid.uuid4(), uuid.uuid4()
SECRET = "s" * 32
SYNC_PATH = "/api/v4/botx/notifications/direct/sync"


def _target() -> BotxTarget:
    return BotxTarget(
        cts_url="https://cts.example.corp/", bot_id=BOT, secret=SecretStr(SECRET), chat_id=CHAT
    )


def _notifier(handler: object) -> BotxNotifier:
    return BotxNotifier(_target(), timeout_s=5, transport=httpx.MockTransport(handler))  # type: ignore[arg-type]


def _ok(_request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"status": "ok", "result": {"sync_id": str(uuid.uuid4())}})


def _failures(module: str) -> float:
    return notify_failures_total.labels(module=module)._value.get()


def test_jwt_matches_botx_v2_format() -> None:
    token = build_botx_jwt(bot_id=BOT, host="cts.example.corp", secret=SECRET, now=1_700_000_000)
    claims = jwt.decode(token, SECRET, algorithms=["HS256"], audience="cts.example.corp",
                        options={"verify_exp": False, "verify_nbf": False})
    assert claims["iss"] == str(BOT)
    assert claims["version"] == 2
    assert claims["exp"] - claims["iat"] == 60
    assert claims["nbf"] == claims["iat"] == 1_700_000_000
    assert claims["jti"]


def test_posts_sync_notification_with_signed_jwt() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return _ok(request)

    text = "✅ Иванов Иван\nЗаписался на 05.10.2026 в 11:00\nПсихолог"
    _notifier(handler).send(text)

    assert [(r.method, r.url.host, r.url.path) for r in seen] == [
        ("POST", "cts.example.corp", SYNC_PATH)
    ]
    body = json.loads(seen[0].content)
    assert body["group_chat_id"] == str(CHAT)
    assert body["notification"]["status"] == "ok"
    assert body["notification"]["body"] == text
    token = seen[0].headers["Authorization"].removeprefix("Bearer ")
    claims = jwt.decode(token, SECRET, algorithms=["HS256"], audience="cts.example.corp")
    assert claims["iss"] == str(BOT)


def test_http_error_raises() -> None:
    with pytest.raises(BotxSendError, match="500"):
        _notifier(lambda _r: httpx.Response(500, text="boom")).send("x")


def test_error_status_in_body_raises() -> None:
    """Синхронный endpoint сообщает о сбое доставки телом, а не HTTP-кодом."""
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "error", "reason": "bot_is_not_a_chat_member"})

    with pytest.raises(BotxSendError, match="bot_is_not_a_chat_member"):
        _notifier(handler).send("x")


def test_network_error_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(BotxSendError, match="ConnectError"):
        _notifier(handler).send("x")


def test_notify_safely_swallows_and_counts() -> None:
    before = _failures("library")
    notify_safely(FakeNotifier(fail=True), "x", module="library")
    assert _failures("library") == before + 1


def test_notify_safely_counts_missing_notifier() -> None:
    before = _failures("appointments")
    notify_safely(None, "x", module="appointments")
    assert _failures("appointments") == before + 1


def test_notify_safely_delivers() -> None:
    fake = FakeNotifier()
    notify_safely(fake, "привет", module="library")
    assert fake.sent == ["привет"]


def test_target_is_none_until_fully_configured() -> None:
    s = Settings(_env_file=None, botx_cts_url="https://cts", library_bot_id=str(BOT))
    assert botx_target(s, "library") is None
    assert make_notifier(s, "library") is None


def test_target_per_module() -> None:
    s = Settings(
        _env_file=None,
        botx_cts_url="https://cts",
        library_bot_id=str(BOT),
        library_bot_secret=SecretStr("s"),
        library_notify_chat_id=str(CHAT),
    )
    target = botx_target(s, "library")
    assert target is not None and target.chat_id == CHAT
    assert botx_target(s, "appointments") is None
```

Run: `pytest tests/unit/test_notify.py -q` → FAIL (нет модуля).

- [ ] **Step 3: `src/pa_booking/notify/botx.py`** (`notify/__init__.py` — пустой)

```python
"""Уведомления в служебные чаты eXpress через BotX (спека Р-5, §5.4).

Шлём от аккаунтов прод-ботов (``psy_bot_v2`` → записи, ``Library_bot`` → библиотека)
синхронным методом ``POST /api/v4/botx/notifications/direct/sync``: результат
приходит в ответе, колбэков нет — нам не нужен приёмник вебхуков. Тот же метод
использует ``express_notif``. JWT собираем сами — формат как у
``pybotx.auth.build_botx_jwt_v2``.

``Notifier.send`` при сбое бросает; вызывающий код зовёт ``notify_safely``, который
сбой логирует и считает, но наружу не пускает — операция уже закоммичена.
"""

from __future__ import annotations

import secrets
import time
import uuid
from dataclasses import dataclass
from typing import Literal, Protocol
from urllib.parse import urlparse

import httpx
import jwt
import structlog
from pydantic import SecretStr

from pa_booking.core.config import Settings
from pa_booking.core.metrics import notify_failures_total

log = structlog.get_logger(__name__)

Module = Literal["appointments", "library"]

SYNC_NOTIFICATION_PATH = "/api/v4/botx/notifications/direct/sync"
JWT_TTL_S = 60


class Notifier(Protocol):
    def send(self, text: str) -> None: ...


class BotxSendError(RuntimeError):
    """BotX не принял уведомление: сеть, HTTP-ошибка или ``status: error`` в теле."""


@dataclass(frozen=True)
class BotxTarget:
    cts_url: str
    bot_id: uuid.UUID
    secret: SecretStr
    chat_id: uuid.UUID


def botx_target(settings: Settings, module: Module) -> BotxTarget | None:
    """Куда и от чьего имени слать для модуля; None — BotX для модуля не настроен."""
    if module == "appointments":
        bot_id, secret, chat_id = (
            settings.appointments_bot_id,
            settings.appointments_bot_secret,
            settings.appointments_notify_chat_id,
        )
    else:
        bot_id, secret, chat_id = (
            settings.library_bot_id,
            settings.library_bot_secret,
            settings.library_notify_chat_id,
        )
    if not (settings.botx_cts_url and bot_id and secret.get_secret_value() and chat_id):
        return None
    return BotxTarget(
        cts_url=settings.botx_cts_url,
        bot_id=uuid.UUID(bot_id),
        secret=secret,
        chat_id=uuid.UUID(chat_id),
    )


def build_botx_jwt(*, bot_id: uuid.UUID, host: str, secret: str, now: int) -> str:
    """JWT авторизации бота в BotX (версия 2): подписывается секретом бота локально."""
    payload = {
        "iss": str(bot_id),
        "aud": host,
        "exp": now + JWT_TTL_S,
        "nbf": now,
        "iat": now,
        "jti": secrets.token_hex(12),
        "version": 2,
    }
    return jwt.encode(payload, secret, algorithm="HS256")


class BotxNotifier:
    """Отправка текста в чат от имени бота."""

    def __init__(
        self,
        target: BotxTarget,
        *,
        timeout_s: float,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        host = urlparse(target.cts_url).hostname
        if not host:
            raise ValueError("не удалось разобрать хост из PA_BOOKING_BOTX_CTS_URL")
        self._target = target
        self._host = host
        self._url = target.cts_url.rstrip("/") + SYNC_NOTIFICATION_PATH
        self._timeout_s = timeout_s
        self._transport = transport

    def send(self, text: str) -> None:
        token = build_botx_jwt(
            bot_id=self._target.bot_id,
            host=self._host,
            secret=self._target.secret.get_secret_value(),
            now=int(time.time()),
        )
        payload = {
            "group_chat_id": str(self._target.chat_id),
            "notification": {"status": "ok", "body": text},
        }
        try:
            with httpx.Client(timeout=self._timeout_s, transport=self._transport) as client:
                response = client.post(
                    self._url, json=payload, headers={"Authorization": f"Bearer {token}"}
                )
        except httpx.HTTPError as exc:
            raise BotxSendError(f"{type(exc).__name__}: {exc}") from exc
        if response.is_error:
            raise BotxSendError(f"BotX ответил {response.status_code}: {response.text[:200]}")
        try:
            body = response.json()
        except ValueError as exc:
            raise BotxSendError(f"BotX вернул не JSON: {response.text[:200]}") from exc
        if body.get("status") != "ok":
            raise BotxSendError(f"BotX отклонил уведомление: {body.get('reason')}")


class FakeNotifier:
    """Двойник для тестов: копит тексты или падает по заказу."""

    def __init__(self, *, fail: bool = False) -> None:
        self.sent: list[str] = []
        self._fail = fail

    def send(self, text: str) -> None:
        if self._fail:
            raise BotxSendError("FakeNotifier: сбой отправки")
        self.sent.append(text)


def make_notifier(settings: Settings, module: Module) -> Notifier | None:
    target = botx_target(settings, module)
    if target is None:
        return None
    return BotxNotifier(target, timeout_s=settings.botx_timeout_s)


def notify_safely(notifier: Notifier | None, text: str, *, module: Module) -> None:
    """Отправить, а при любом сбое — warning в лог и +1 в метрику. Не бросает.

    Текст в лог не пишем: в нём ФИО и тип записи (чувствительные данные).
    """
    if notifier is None:
        log.warning("notify_skipped", module=module, reason="botx_not_configured")
        notify_failures_total.labels(module=module).inc()
        return
    try:
        notifier.send(text)
    except Exception as exc:
        log.warning("notify_failed", module=module, error=f"{type(exc).__name__}: {exc}")
        notify_failures_total.labels(module=module).inc()
```

- [ ] **Step 4:** `pytest tests/unit/test_notify.py -q` → PASS; `ruff check . && mypy src`.

- [ ] **Step 5: `external`-тест** — `tests/external/test_botx.py`
  (`tests/external/__init__.py` — пустой). Настройки берёт из корневого `.env`, поэтому
  снимает изоляцию из `tests/conftest.py`:

```python
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from pa_booking.core.config import Settings
from pa_booking.notify.botx import Module, make_notifier

pytestmark = pytest.mark.external


@pytest.mark.parametrize("module", ["appointments", "library"])
def test_sends_to_service_chat(module: Module, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(Settings.model_config, "env_file", ".env")
    notifier = make_notifier(Settings(), module)
    assert notifier is not None, f"BotX для {module} не настроен в .env"
    stamp = datetime.now(ZoneInfo("Europe/Moscow")).strftime("%d.%m.%Y %H:%M")
    notifier.send(f"[ТЕСТ ЛК, external-тест, {stamp} МСК] ✅ проверка уведомлений\nстрока 2")
```

**Пользователь** (по сообщению в оба служебных чата; значения — из `spikes/botx/.env`,
имена совпадают): `pytest -m external tests/external/test_botx.py -q`. Этот тест
заменяет Блок 0: зелёный прогон = учётки, сеть и метод рабочие. После него —
`spikes/` удалить. Вопрос «работает ли при выключенном боте» — тот же тест при
остановленном `Library_bot` или в день отключения ботов.

- [ ] **Step 6: коммит** — `feat(notify): add BotX notifier with safe send`

### Task 1.6: приложение — health, ready, metrics

**Files:**
- Create: `src/pa_booking/core/http_metrics.py`, `src/pa_booking/api/__init__.py`,
  `src/pa_booking/api/deps.py`, `src/pa_booking/app.py`
- Test: `tests/unit/test_app_health.py`

**Interfaces:**
- Produces: `create_app() -> FastAPI`, `app`; `get_session` (503 `db_not_configured`
  без DSN); алиасы `DbSession`, `Principal`, `PsychologistPrincipal`,
  `LibrarianPrincipal`.

- [ ] **Step 1: падающий тест** — `tests/unit/test_app_health.py`: копия
  `overtimes/tests/unit/test_app_health.py` с заменами `pa_overtimes` → `pa_booking`;
  в `test_metrics_exposes_prometheus_text` вместо `client.get("/v1/lines")` —
  `client.get("/nope")`; во всех `/ready`-тестах тело проверяется как
  `r.json()["detail"]`, плюс в `test_ready_without_dsn_reports_not_ready`:

```python
    assert r.json()["code"] == "not_ready"
```

Run: `pytest tests/unit/test_app_health.py -q` → FAIL.

- [ ] **Step 2: `src/pa_booking/core/http_metrics.py`** — копия `overtimes/core/http_metrics.py`,
  замена импорта `pa_overtimes` → `pa_booking`.

- [ ] **Step 3: `src/pa_booking/api/deps.py`** (`api/__init__.py` — пустой)

```python
"""Общие зависимости FastAPI: сессия БД и принципал."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from pa_booking.core.auth import MeshPrincipal, get_current_user, require_role
from pa_booking.core.errors import ApiError
from pa_booking.domain.roles import Role


def get_session(request: Request) -> Iterator[Session]:
    """Сессия на запрос. Коммитит вызывающая ручка — транзакция явная."""
    sessionmaker_ = getattr(request.app.state, "sessionmaker", None)
    if sessionmaker_ is None:
        raise ApiError(503, "db_not_configured", "База данных не настроена")
    with sessionmaker_() as session:
        yield session


DbSession = Annotated[Session, Depends(get_session)]
Principal = Annotated[MeshPrincipal, Depends(get_current_user)]
PsychologistPrincipal = Annotated[MeshPrincipal, Depends(require_role(Role.PSYCHOLOGIST))]
LibrarianPrincipal = Annotated[MeshPrincipal, Depends(require_role(Role.LIBRARIAN))]
```

- [ ] **Step 4: `src/pa_booking/app.py`** — копия `overtimes/app.py` с правками:
  импорты `pa_overtimes` → `pa_booking`; роутеров пока нет (строки `include_router`
  и их импорты убрать); `title="PA Booking Service"`, описание — записи и библиотека;
  после `install_http_metrics(app)` — `install_error_handlers(app)`; в `/ready`
  вместо `raise HTTPException(...)` — ответ в общем формате:

```python
        if any(v != "ok" for v in checks.values()):
            return JSONResponse(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                content={"detail": checks, "code": "not_ready"},
            )
        return JSONResponse(content=checks)
```

  (сигнатура `ready() -> JSONResponse`; `HTTPException` из импортов убрать,
  `from fastapi.responses import JSONResponse, Response`).

- [ ] **Step 5:** `pytest tests/unit -q` → PASS; `ruff check . && mypy src` чистые.

- [ ] **Step 6: коммит** — `feat(app): add health, readiness and metrics endpoints`

### Task 1.7: Celery-воркер и docker

**Files:**
- Create: `src/pa_booking/workers/__init__.py`, `src/pa_booking/workers/celery_app.py`,
  `src/pa_booking/workers/sync_roster.py`, `src/pa_booking/workers/metrics_server.py`,
  `docker/Dockerfile`, `docker/docker-compose.yml`
- Test: `tests/unit/test_celery_app.py`, `tests/unit/test_sync_roster.py`

**Interfaces:**
- Produces: `celery_app` (очередь `pa_booking`, beat `sync-roster`),
  задача `pa_booking.sync_roster`, `sync_roster_once(session, client, *, api_key, now) -> int`
  (пустой ростер → `DirectoryFetchError`, снимок не трогается).

- [ ] **Step 1: падающие тесты**

`tests/unit/test_celery_app.py`: копия `overtimes/tests/unit/test_celery_app.py` с заменами
`pa_overtimes` → `pa_booking` (имя задачи `pa_booking.sync_roster`, очередь `pa_booking`).

`tests/unit/test_sync_roster.py`:

```python
from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pytest

from pa_booking.directory.roster import DirectoryFetchError
from pa_booking.workers.sync_roster import sync_roster_once


class _Client:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._body = json.dumps(payload).encode()

    def get(self, path: str, *, headers: dict[str, str]) -> tuple[int, bytes]:
        return 200, self._body


class _Session:
    def __init__(self) -> None:
        self.committed = False
        self.executed = 0

    def execute(self, *_a: object, **_k: object) -> None:
        self.executed += 1

    def add_all(self, *_a: object) -> None: ...

    def add(self, *_a: object) -> None: ...

    def get(self, *_a: object) -> None:
        return None

    def commit(self) -> None:
        self.committed = True


def test_empty_roster_does_not_wipe_snapshot() -> None:
    """Пустой ответ auth — сбой, а не «все уволились»: старые ФИО остаются."""
    session = _Session()
    with pytest.raises(DirectoryFetchError):
        sync_roster_once(
            session,  # type: ignore[arg-type]
            _Client({"operators": [], "employees": []}),
            api_key="k",
            now=datetime.now(UTC),
        )
    assert session.executed == 0
    assert not session.committed
```

Run: `pytest tests/unit/test_celery_app.py tests/unit/test_sync_roster.py -q` → FAIL.

- [ ] **Step 2: `workers/celery_app.py`, `workers/metrics_server.py`** — копии из
  `overtimes` с заменами `pa_overtimes` → `pa_booking` (имя приложения, очередь,
  `TASK_MODULES`, имя задачи в `beat_schedule`); `timezone="Europe/Moscow"` —
  константой вместо `settings.celery_timezone` (такой настройки у нас нет: время
  сервиса — всегда МСК). `workers/__init__.py` — пустой.

- [ ] **Step 3: `workers/sync_roster.py`** — копия из `overtimes` с заменами
  `pa_overtimes` → `pa_booking`, `roster_operators` → `roster_employees`; в
  `sync_roster_once` — защита от пустого ростера:

```python
def sync_roster_once(
    session: Session, client: HttpClient, *, api_key: str, now: datetime
) -> int:
    """Забрать ростер и заменить снимок. Возвращает число сотрудников.

    Пустой ростер не сохраняем: это сбой на стороне auth, а не «все уволились» —
    иначе пропали бы ФИО во всех уведомлениях и выгрузках.
    """
    rows = flatten(fetch_roster_snapshot(client, api_key=api_key))
    if not rows:
        raise DirectoryFetchError("ростер пуст — снимок не обновляем")
    save_snapshot(session, rows, now=now)
    session.commit()
    return len(rows)
```

- [ ] **Step 4: docker.** `docker/Dockerfile` — копия из `overtimes`; без `README.md` в
  `COPY`, `CMD` → `pa_booking.app:app`; `EXPOSE` не нужен (host-порта нет).
  `docker/docker-compose.yml` — копия из `overtimes`; замены: `name: pa_booking`,
  `pa_overtimes` → `pa_booking` в командах, алиасы `overtimes-api` → `booking-api`,
  `overtimes-worker` → `booking-worker`. Портов наружу нет ни у одного сервиса.

- [ ] **Step 5:** `pytest tests/unit -q` → PASS; `ruff check . && mypy src` чистые;
  `docker compose -f docker/docker-compose.yml config -q` — синтаксис compose.
  **Пользователь (на хосте с `pa_net`):** `docker compose -f docker/docker-compose.yml
  --profile tools run --rm migrate` и `docker compose -f docker/docker-compose.yml up -d
  --build`; затем `docker compose -f docker/docker-compose.yml exec api python -c
  "import urllib.request;print(urllib.request.urlopen('http://localhost:8000/ready').read())"`.
  Ожидаем `{"database":"ok","roster":"ok"}` после первого синка ростера
  (до него — 503 `never synced`).

- [ ] **Step 6: коммит** — `feat(workers): add celery roster sync and compose stack`

**Полный прогон блока (пользователь):** `pytest -m "not external" -q` (юнит + `db`).

---

## Блок 2. Модуль записей — скоуп

Домен §4.1 (лимит 4/месяц, замок 14:00 МСК, отмены, обзор ±3 дня; Д-8, Д-9),
таблицы с частичными уникальными индексами, API §5.1, чистка 14:00 + догоняющий
прогон, уведомления §5.5 через `notify_safely`, `export/xlsx.py` + выгрузки.

## Блок 3. Модуль библиотеки — скоуп

Домен §4.2, таблицы, API §5.2, уведомления, xlsx истории.

## Блок 4. BFF + SPA — скоуп

§6: `pa_bff/clients/booking.py`, проксирующие роутеры, потоковый xlsx; страницы SPA,
гейты по ролям, ошибки по `code`. Роли `psychologist`/`librarian` в `pa_auth_service`.

## Блок 5. Скрипты переноса — скоуп

§7: каталог и будущие свободные слоты автоматически; CSV для ручного сопоставления
HUID → `employee_id`; ассерты из посчитанных `SELECT COUNT(*)`.
