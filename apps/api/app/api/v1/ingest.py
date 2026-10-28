"""Ingest endpoint - the hot path (FR-010..012, FR-016).

Authentication is the project API key, NOT the session cookie. No CSRF check
is needed because there is no ambient browser credential involved.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.metrics import ingest_requests_total
from app.core.errors import ValidationProblem
from app.db.session import get_session
from app.services.ingest_service import handle_ingest
from dataloom_contracts.ingest import IngestAccepted

router = APIRouter(tags=["ingest"])


@router.post(
    "/ingest/{source_id}",
    response_model=IngestAccepted,
    status_code=202,
    responses={
        401: {"description": "API key tidak valid/dicabut"},
        409: {"description": "Sumber tidak menerima HTTP"},
        413: {"description": "Body atau jumlah baris melebihi batas"},
        429: {"description": "Rate limit atau antrian penuh (Retry-After)"},
        503: {"description": "Antrian tidak tersedia - data TIDAK diterima"},
    },
)
async def ingest(
    source_id: uuid.UUID,
    request: Request,
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_session),
) -> JSONResponse | IngestAccepted:
    """Validate cheaply, enqueue, and answer 202. Never touches Bronze here."""
    content_length = request.headers.get("content-length")
    try:
        declared_bytes = int(content_length) if content_length else None
    except ValueError:  # a non-numeric header must be a 4xx, never a 500
        raise ValidationProblem("Header Content-Length tidak valid.")
    body = await request.body()

    result = await handle_ingest(
        session=session,
        raw_key=x_api_key,
        source_id=source_id,
        content_length=declared_bytes,
        raw_body=body,
        idempotency_key=idempotency_key,
    )

    payload = IngestAccepted(accepted=result.accepted, batch_hint=result.batch_hint)
    headers = {}
    if result.replayed:
        headers["Idempotent-Replay"] = "true"
    return JSONResponse(
        status_code=202, content=payload.model_dump(mode="json"), headers=headers
    )


@router.get("/ingest/health", include_in_schema=False)
async def ingest_health() -> dict[str, str]:
    """Lightweight probe used by external producers before a burst."""
    return {"status": "ok"}
