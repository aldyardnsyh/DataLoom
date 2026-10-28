"""Uniform error handling.

Every error response has the shape required by PRD section 9.7::

    {"code": "...", "message": "...", "request_id": "..."}

`code` is a stable English token used by the UI to pick an icon/message;
`message` is Indonesian prose shown to the user.
"""

from __future__ import annotations

import contextvars
import uuid
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

#: Per-request id, set by the request-id middleware and read by error handlers.
request_id_ctx: contextvars.ContextVar[str] = contextvars.ContextVar(
    "request_id", default="unknown"
)


class AppError(Exception):
    """Base application error carrying an HTTP status and a stable code."""

    status_code: int = 500
    code: str = "internal_error"

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        status_code: int | None = None,
        details: Any = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code
        if status_code is not None:
            self.status_code = status_code
        self.details = details
        self.headers = headers or {}


class Unauthorized(AppError):
    status_code = 401
    code = "unauthorized"


class Forbidden(AppError):
    status_code = 403
    code = "forbidden"


class NotFound(AppError):
    status_code = 404
    code = "not_found"

    def __init__(self, message: str = "Sumber daya tidak ditemukan.") -> None:
        super().__init__(message)


class Conflict(AppError):
    status_code = 409
    code = "conflict"


class PayloadTooLarge(AppError):
    status_code = 413
    code = "payload_too_large"


class TooManyRequests(AppError):
    status_code = 429
    code = "rate_limited"

    def __init__(self, message: str, *, retry_after: int = 1) -> None:
        super().__init__(message, headers={"Retry-After": str(retry_after)})


class ValidationProblem(AppError):
    status_code = 422
    code = "validation_error"


class ServiceUnavailable(AppError):
    status_code = 503
    code = "service_unavailable"


def _body(code: str, message: str, details: Any = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "code": code,
        "message": message,
        "request_id": request_id_ctx.get(),
    }
    if details is not None:
        payload["details"] = details
    return payload


def register_exception_handlers(app: FastAPI) -> None:
    """Attach handlers so no error escapes without the uniform shape."""

    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=_body(exc.code, exc.message, exc.details),
            headers=exc.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        # Never echo raw input back: it may contain secrets (API keys in body).
        issues = [
            {"loc": list(err.get("loc", [])), "msg": err.get("msg", "")}
            for err in exc.errors()
        ]
        return JSONResponse(
            status_code=422,
            content=_body(
                "validation_error", "Data yang dikirim tidak valid.", issues
            ),
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = {
            401: "unauthorized",
            403: "forbidden",
            404: "not_found",
            405: "method_not_allowed",
            413: "payload_too_large",
            429: "rate_limited",
        }.get(exc.status_code, "http_error")
        message = str(exc.detail) if exc.detail else "Terjadi kesalahan."
        return JSONResponse(status_code=exc.status_code, content=_body(code, message))

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception) -> JSONResponse:
        # Log identifier only; never the exception text (it may embed secrets).
        return JSONResponse(
            status_code=500,
            content=_body(
                "internal_error",
                "Terjadi kesalahan internal. Hubungi dukungan dengan ID permintaan.",
            ),
        )


def new_request_id() -> str:
    return uuid.uuid4().hex
