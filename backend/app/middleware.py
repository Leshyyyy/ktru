"""ASGI-middleware: request_id и строка лога на запрос, заголовки безопасности, лимит размера тела.

Написаны на чистом ASGI, а не через BaseHTTPMiddleware: так не ломаются contextvars и потоковые ответы.
"""

from __future__ import annotations

import logging
import re
import time
import uuid

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from backend.core.logs import request_id_var

from .errors import BodyTooLarge, error_response, file_too_large_response, internal_error_response

log = logging.getLogger("backend.http")

REQUEST_ID_HEADER = "X-Request-ID"
# Чужой request_id принимаем, только если он безопасен для логов и заголовков.
_REQUEST_ID_RE = re.compile(r"[A-Za-z0-9._-]{1,64}")
_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Resource-Policy": "same-origin",
    "Cache-Control": "no-store",
}
# Фронт опрашивает их раз в 1–2 секунды — в INFO они только засоряют лог.
_QUIET_PATHS = frozenset({"/api/v1/health", "/api/v1/config"})


class RequestContextMiddleware:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = _incoming_request_id(scope) or uuid.uuid4().hex
        token = request_id_var.set(request_id)
        started_at = time.perf_counter()
        status = 500
        response_started = False

        async def send_with_headers(message: Message) -> None:
            nonlocal status, response_started
            if message["type"] == "http.response.start":
                response_started = True
                status = message["status"]
                headers = MutableHeaders(scope=message)
                headers[REQUEST_ID_HEADER] = request_id
                for name, value in _SECURITY_HEADERS.items():
                    headers.setdefault(name, value)
            await send(message)

        try:
            await self.app(scope, receive, send_with_headers)
        except Exception:
            log.exception("Необработанная ошибка при обработке запроса", extra={"path": scope["path"]})
            if response_started:
                raise  # ответ уже начат — остаётся только оборвать соединение
            await internal_error_response(request_id)(scope, receive, send_with_headers)
        finally:
            level = logging.DEBUG if scope["path"] in _QUIET_PATHS and status < 400 else logging.INFO
            log.log(
                level,
                "HTTP-запрос",
                extra={
                    "method": scope["method"],
                    "path": scope["path"],
                    "status": status,
                    "duration_ms": round((time.perf_counter() - started_at) * 1000, 1),
                },
            )
            request_id_var.reset(token)


def _incoming_request_id(scope: Scope) -> str | None:
    for name, value in scope["headers"]:
        if name == b"x-request-id":
            candidate = value.decode("latin-1")
            return candidate if _REQUEST_ID_RE.fullmatch(candidate) else None
    return None


class BodySizeLimitMiddleware:
    """Отсекает тела больше max_bytes: по Content-Length сразу, без него — по мере чтения.

    nginx пропускает до 60 МБ, а свои 50 МБ на файл приложение проверяет само и отвечает 413 в нашем формате.
    """

    def __init__(self, app: ASGIApp, *, max_bytes: int, max_file_mb: int):
        self.app = app
        self.max_bytes = max_bytes
        self.max_file_mb = max_file_mb

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        for name, value in scope["headers"]:
            if name == b"content-length":
                try:
                    declared = int(value)
                except ValueError:
                    response = error_response(400, "invalid_request", "Некорректный заголовок Content-Length.")
                    await response(scope, receive, send)
                    return
                if declared > self.max_bytes:
                    await file_too_large_response(self.max_file_mb)(scope, receive, send)
                    return

        received = 0
        response_started = False

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    raise BodyTooLarge
            return message

        async def tracking_send(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, tracking_send)
        except BodyTooLarge:
            if response_started:
                raise
            await file_too_large_response(self.max_file_mb)(scope, receive, send)
