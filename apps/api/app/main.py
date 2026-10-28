"""FastAPI application factory."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.api.v1 import api_router
from app.config import get_settings
from app.core.errors import new_request_id, register_exception_handlers, request_id_ctx
from app.core.logging_redaction import install_log_redaction
from app.core.metrics import metrics_response
from app.core.ratelimit import close_redis, get_redis

logger = logging.getLogger("dataloom.api")


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Attach a request id to every response and to the error envelope."""

    async def dispatch(self, request: Request, call_next):  # noqa: ANN001
        incoming = request.headers.get("x-request-id")
        rid = incoming if incoming and len(incoming) <= 64 else new_request_id()
        token = request_id_ctx.set(rid)
        try:
            response = await call_next(request)
        finally:
            request_id_ctx.reset(token)
        response.headers["X-Request-ID"] = rid
        return response


class BodySizeLimitMiddleware(BaseHTTPMiddleware):
    """Hard cap on ingest bodies before the handler runs (FR-012)."""

    async def dispatch(self, request: Request, call_next):  # noqa: ANN001
        if request.url.path.startswith("/v1/ingest/"):
            settings = get_settings()
            length = request.headers.get("content-length")
            try:
                declared = int(length) if length is not None else None
            except ValueError:  # malformed header -> 4xx via the error envelope
                from app.core.errors import ValidationProblem

                raise ValidationProblem("Header Content-Length tidak valid.") from None
            if declared is not None and declared > settings.INGEST_MAX_BODY_BYTES:
                from app.core.errors import PayloadTooLarge

                raise PayloadTooLarge(
                    f"Ukuran permintaan melebihi batas {settings.INGEST_MAX_BODY_BYTES} byte."
                )
        return await call_next(request)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Startup/shutdown: create clients lazily, close them cleanly."""
    logging.basicConfig(level=get_settings().LOG_LEVEL)
    install_log_redaction()
    get_redis()  # warm the pool (no network call until first command)
    logger.info("DataLoom API starting env=%s", get_settings().ENV)

    from app.services.scheduler import start_scheduler, stop_scheduler

    start_scheduler()
    try:
        yield
    finally:
        stop_scheduler()
        await close_redis()
        logger.info("DataLoom API stopped")


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="DataLoom API",
        version="0.1.0",
        description=(
            "AI-orchestrated data pipeline platform. Hot path is deterministic "
            "and LLM-free. All error bodies use {code, message, request_id}."
        ),
        lifespan=lifespan,
        docs_url="/docs" if settings.ENV != "production" else None,
        redoc_url=None,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "X-CSRF-Token", "X-API-Key", "Idempotency-Key"],
        expose_headers=["X-Request-ID", "Retry-After", "Idempotent-Replay"],
    )
    app.add_middleware(GZipMiddleware, minimum_size=1024)
    app.add_middleware(RequestIdMiddleware)
    app.add_middleware(BodySizeLimitMiddleware)
    if settings.TRUSTED_HOSTS and "*" not in settings.TRUSTED_HOSTS:
        app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.TRUSTED_HOSTS)

    register_exception_handlers(app)
    app.include_router(api_router)

    @app.get("/healthz", tags=["ops"])
    async def healthz() -> dict[str, object]:
        """Liveness + dependency probe. Reports booleans, never secrets."""
        from sqlalchemy import text

        from app.db.session import get_session_factory

        checks: dict[str, bool] = {}
        try:
            async with get_session_factory()() as session:
                await session.execute(text("SELECT 1"))
            checks["database"] = True
        except Exception:  # noqa: BLE001
            checks["database"] = False
        try:
            await get_redis().ping()
            checks["redis"] = True
        except Exception:  # noqa: BLE001
            checks["redis"] = False
        status = "ok" if all(checks.values()) else "degraded"
        return {"status": status, "checks": checks}

    @app.get("/metrics", tags=["ops"], include_in_schema=False)
    async def metrics():  # noqa: ANN202
        """Prometheus exposition (FR-042)."""
        return metrics_response()

    return app


app = create_app()
