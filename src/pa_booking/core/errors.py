"""Единый формат ошибок API (спека §5.4): ``{"detail": "<по-русски>", "code": "<машинный>"}``.

SPA ветвится по ``code``, человеку показывает ``detail``. Сюда же сводятся ошибки
самого FastAPI (404 неизвестного пути, 422 Pydantic), чтобы формат был один.
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from pa_booking.domain.errors import DomainError

_HTTP_DEFAULTS: dict[int, tuple[str, str]] = {
    401: ("unauthorized", "Требуется аутентификация"),
    403: ("forbidden", "Недостаточно прав"),
    404: ("not_found", "Не найдено"),
    405: ("method_not_allowed", "Метод не поддерживается"),
}

# Нарушение бизнес-правила — 409 (спека §5.4); исключения — по коду.
_DOMAIN_STATUS: dict[str, int] = {"slot_date_in_past": 422}


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

    @app.exception_handler(DomainError)
    async def _domain_error(_request: Request, exc: DomainError) -> JSONResponse:
        status_code = _DOMAIN_STATUS.get(exc.code, 409)
        return JSONResponse(status_code=status_code, content=_body(exc.code, exc.detail))

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
