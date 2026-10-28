"""Ingest hot-path contracts (FR-010, FR-011, FR-012, FR-016).

The response body is deliberately tiny: the hot path must not serialize
anything heavier than two scalars.
"""

from __future__ import annotations

from pydantic import Field

from ._base import ReadModel


class IngestAccepted(ReadModel):
    """202 body of ``POST /v1/ingest/{source_id}`` (PRD section 9.4 step 6).

    ``accepted`` is the number of rows queued, not yet written to bronze.
    ``batch_hint`` is the stream entry id the worker will batch under; it is
    opaque to clients and useful only for support/debugging.
    """

    accepted: int = Field(ge=0, description="Number of rows accepted into the stream.")
    batch_hint: str = Field(
        description="Opaque stream id / batch hint, e.g. '1745678901234-0'."
    )


class IngestRejected(ReadModel):
    """413/429 body. Kept separate from IngestAccepted so the hot path stays tiny."""

    code: str
    message: str
    max_rows: int | None = None
    max_bytes: int | None = None


class IdempotencyRecord(ReadModel):
    """Shape of the row in ``ingest_idempotency`` (FR-016, 24h window)."""

    key: str = Field(description="Client-supplied Idempotency-Key header.")
    source_id: str
    request_hash: str = Field(description="SHA-256 of the body, to detect reuse.")
    response_accepted: int = Field(ge=0)
    response_batch_hint: str
    created_at: str


__all__ = ["IdempotencyRecord", "IngestAccepted", "IngestRejected"]
