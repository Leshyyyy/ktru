"""Единый формат ошибок API: {"error": {"code", "message", "details"}}, message — по-русски и для человека.

В маршрутах ошибки бросаются только как ApiError. Ошибки валидации запроса — 400 invalid_request (а не 422
FastAPI в формате {"detail": [...]}); 422 у нас означает только бизнес-ошибки (раздел 5 аналитики).
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any, Literal

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

log = logging.getLogger(__name__)

ErrorCode = Literal[
    "invalid_request",
    "not_found",
    "invalid_state",
    "file_too_large",
    "unsupported_file_type",
    "file_unreadable",
    "mapping_invalid",
    "limit_exceeded",
    "unreviewed_items",
    "not_ready",
    "internal_error",
]


class ApiError(Exception):
    def __init__(
        self,
        status: int,
        code: ErrorCode,
        message: str,
        details: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.details = details
        self.headers = headers


def not_found(obj: str, message: str) -> ApiError:
    return ApiError(404, "not_found", message, {"object": obj})


def invalid_state(message: str, **details: Any) -> ApiError:
    return ApiError(409, "invalid_state", message, details or None)


def not_ready() -> ApiError:
    return ApiError(503, "not_ready", "Система ещё готовится. Повторите через минуту.", headers={"Retry-After": "30"})


class BodyTooLarge(Exception):
    """Тело запроса больше лимита; бросает BodySizeLimitMiddleware при чтении тела."""


def error_response(
    status: int,
    code: ErrorCode,
    message: str,
    details: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    body = {"error": {"code": code, "message": message, "details": details}}
    return JSONResponse(body, status_code=status, headers=headers)


def internal_error_response(request_id: str | None) -> JSONResponse:
    # Текст исключения наружу не отдаём: он остаётся в логе под этим request_id.
    return error_response(
        500,
        "internal_error",
        "Внутренняя ошибка сервера. Повторите попытку; если ошибка не уходит, сообщите код запроса.",
        {"request_id": request_id},
    )


def file_too_large_response(max_file_mb: int) -> JSONResponse:
    return error_response(413, "file_too_large", f"Файл больше {max_file_mb} МБ. Разбейте его на части.")


def install_error_handlers(app: FastAPI, *, max_file_mb: int) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError) -> JSONResponse:
        return error_response(exc.status, exc.code, exc.message, exc.details, exc.headers)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        message, field = validation_message(exc.errors())
        return error_response(400, "invalid_request", message, {"field": field} if field else None)

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        # FastAPI превращает любую ошибку чтения тела в 400; превышение лимита должно остаться 413.
        if isinstance(exc.__cause__, BodyTooLarge):
            return file_too_large_response(max_file_mb)
        if exc.status_code == 404:
            return error_response(404, "not_found", "Адрес не найден.", {"object": "route"})
        if exc.status_code == 405:
            return error_response(405, "invalid_request", "Этот метод для адреса не поддерживается.", None, exc.headers)
        if exc.status_code == 400:
            return error_response(400, "invalid_request", "Некорректный запрос: не удалось прочитать тело запроса.")
        if exc.status_code < 500:
            return error_response(exc.status_code, "invalid_request", "Некорректный запрос.", None, exc.headers)
        log.error("HTTP-ошибка сервера", extra={"status": exc.status_code})
        return internal_error_response(None)


# --- Сообщения валидации по-русски -------------------------------------------------------------------------


def validation_message(errors: Sequence[Any]) -> tuple[str, str | None]:
    """Первая ошибка pydantic → («Некорректный запрос: …», имя поля)."""
    if not errors:
        return "Некорректный запрос.", None
    error = errors[0]
    loc = [str(part) for part in error.get("loc", ())]
    field = ".".join(loc[1:]) or None  # loc[0] — где поле: body, query, path, header
    subject = f"поле {field}" if field else "тело запроса"
    return f"Некорректный запрос: {_describe(error, subject)}.", field


def _describe(error: dict[str, Any], subject: str) -> str:
    kind = error.get("type", "")
    ctx = error.get("ctx") or {}
    if kind == "missing":
        return f"{subject} обязательно"
    if kind == "json_invalid":
        return "тело запроса — некорректный JSON"
    if kind in ("int_parsing", "int_type", "int_from_float"):
        return f"{subject} должно быть целым числом"
    if kind in ("float_parsing", "float_type", "decimal_parsing"):
        return f"{subject} должно быть числом"
    if kind in ("bool_parsing", "bool_type"):
        return f"{subject} должно быть true или false"
    if kind in ("uuid_parsing", "uuid_type", "uuid_version"):
        return f"{subject} должно быть UUID"
    if kind in ("string_type", "string_unicode"):
        return f"{subject} должно быть строкой"
    if kind in ("list_type", "tuple_type", "set_type"):
        return f"{subject} должно быть списком"
    if kind in ("dict_type", "model_type", "model_attributes_type"):
        return f"{subject} должно быть JSON-объектом"
    if kind in ("datetime_parsing", "datetime_type", "datetime_from_date_parsing", "date_parsing", "date_type"):
        return f"{subject} должно быть датой в формате ISO 8601"
    if kind == "greater_than_equal":
        return f"{subject} должно быть не меньше {ctx.get('ge')}"
    if kind == "greater_than":
        return f"{subject} должно быть больше {ctx.get('gt')}"
    if kind == "less_than_equal":
        return f"{subject} должно быть не больше {ctx.get('le')}"
    if kind == "less_than":
        return f"{subject} должно быть меньше {ctx.get('lt')}"
    if kind == "too_short":
        n = ctx.get("min_length", 0)
        return f"{subject} должно содержать не меньше {n} {plural(n, 'элемента', 'элементов', 'элементов')}"
    if kind == "too_long":
        n = ctx.get("max_length", 0)
        return f"{subject} должно содержать не больше {n} {plural(n, 'элемента', 'элементов', 'элементов')}"
    if kind == "string_too_short":
        n = ctx.get("min_length", 0)
        return f"{subject} должно быть не короче {n} {plural(n, 'символа', 'символов', 'символов')}"
    if kind == "string_too_long":
        n = ctx.get("max_length", 0)
        return f"{subject} должно быть не длиннее {n} {plural(n, 'символа', 'символов', 'символов')}"
    if kind in ("literal_error", "enum"):
        expected = str(ctx.get("expected", "")).replace(" or ", ", ")
        return f"{subject} должно быть одним из: {expected}"
    if kind == "extra_forbidden":
        return f"{subject} лишнее"
    if kind == "value_error":
        # Свои валидаторы пишут сообщение по-русски; pydantic добавляет к нему «Value error, ».
        text = str(error.get("msg", "")).removeprefix("Value error, ").rstrip(".")
        return f"{subject}: {text}" if text else f"{subject} заполнено неверно"
    return f"{subject} заполнено неверно"


def plural(n: int, one: str, few: str, many: str) -> str:
    """Форма слова после числа: 1 позиция, 2 позиции, 5 позиций (после «не меньше N» — родительный падеж)."""
    n = abs(int(n))
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many
