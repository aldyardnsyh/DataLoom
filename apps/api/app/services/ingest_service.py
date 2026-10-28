"""Ingest hot path (FR-010, FR-011, FR-012, FR-016).

This module is the ONLY place that talks to the ingest Redis Stream. It is
deliberately free of heavy validation, ORM joins and LLM calls: PRD section
9.4 requires the hot path to stay deterministic and cheap.

Check order (must not change, each step narrows the reason for rejection):
    size -> api key -> source type -> rate limit -> parse/rows
    -> idempotency -> backpressure -> XADD -> 202
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core import metrics
from app.core.errors import (
    Conflict,
    PayloadTooLarge,
    ServiceUnavailable,
    TooManyRequests,
    Unauthorized,
    ValidationProblem,
)
from app.core.ratelimit import (
    get_redis,
    guard_backpressure,
    ingest_rate_limit_ok,
    stream_key,
)
from app.core.security import parse_api_key, sha256_hex
from app.db.models import ApiKey, IngestIdempotency, Project, Source

# Simple in-process cache for API-key lookups (60 s TTL, per PRD 9.4 step 2).
_KEY_CACHE: dict[str, tuple[float, "ApiKeyInfo"]] = {}
_CACHE_MAX_ENTRIES = 10_000


@dataclass(frozen=True, slots=True)
class ApiKeyInfo:
    key_id: uuid.UUID
    org_id: uuid.UUID
    project_id: uuid.UUID
    source_id: uuid.UUID
    source_type: str
    prefix: str


@dataclass(frozen=True, slots=True)
class IngestResult:
    accepted: int
    batch_hint: str
    batch_id: str
    replayed: bool


def _cache_get(prefix: str) -> ApiKeyInfo | None:
    entry = _KEY_CACHE.get(prefix)
    if entry is None:
        return None
    expires_at, info = entry
    if expires_at < time.monotonic():
        _KEY_CACHE.pop(prefix, None)
        return None
    return info


def _cache_put(prefix: str, info: ApiKeyInfo, ttl: int) -> None:
    if len(_KEY_CACHE) > _CACHE_MAX_ENTRIES:
        _KEY_CACHE.clear()  # bounded memory; a miss only costs one query
    _KEY_CACHE[prefix] = (time.monotonic() + ttl, info)


def invalidate_api_key_cache(prefix: str | None = None) -> None:
    """Called on revoke/rotate so a revoked key stops working immediately."""
    if prefix is None:
        _KEY_CACHE.clear()
    else:
        _KEY_CACHE.pop(prefix, None)


# ---------------------------------------------------------------------------
# 1. Body limit (before any JSON parsing)
# ---------------------------------------------------------------------------
def enforce_body_size(content_length: int | None) -> None:
    settings = get_settings()
    if content_length is None:
        return  # streamed/chunked bodies are re-checked by middleware
    if content_length < 0 or content_length > settings.INGEST_MAX_BODY_BYTES:
        metrics.ingest_rejected_total.labels(reason="body_too_large").inc()
        raise PayloadTooLarge(
            f"Ukuran permintaan melebihi batas {settings.INGEST_MAX_BODY_BYTES} byte."
        )


# ---------------------------------------------------------------------------
# 2. API key authentication
# ---------------------------------------------------------------------------
async def _check_key_source_binding(session: AsyncSession, info: ApiKeyInfo) -> None:
    """Verify a cache-hit still points at a live source of the same org.

    The in-process cache is per-worker, so a revoke can only invalidate the
    process that handled it. Without this check a revoked key keeps working on
    every other replica until the TTL expires. Cost: one indexed lookup.
    """
    row = (
        await session.execute(
            select(Source.org_id, Source.type, Source.project_id).where(
                Source.id == info.source_id
            )
        )
    ).first()
    if (
        row is None
        or row[0] != info.org_id
        or row[2] != info.project_id
        or row[1] != info.source_type
    ):
        invalidate_api_key_cache(info.prefix)
        raise Unauthorized("API key sudah tidak berlaku. Silakan masuk ulang.")


async def authenticate_api_key(
    session: AsyncSession, raw_key: str | None, source_id: uuid.UUID
) -> ApiKeyInfo:
    """Resolve an API key to its source. Revoked/mismatched keys -> 401."""
    if not raw_key:
        raise Unauthorized("Header X-API-Key wajib diisi.")

    parsed = parse_api_key(raw_key)
    if parsed is None:
        raise Unauthorized("Format API key tidak valid.")
    prefix, secret = parsed

    cached = _cache_get(prefix)
    if cached is not None:
        if cached.source_id != source_id:
            raise Unauthorized("API key tidak berlaku untuk sumber ini.")
        await _check_key_source_binding(session, cached)
        return cached

    settings = get_settings()
    row = (
        await session.execute(
            select(ApiKey, Source, Project)
            .join(Source, Source.project_id == ApiKey.project_id)
            .join(Project, Project.id == ApiKey.project_id)
            .where(ApiKey.prefix == prefix)
        )
    ).first()

    if row is None:
        raise Unauthorized("API key tidak dikenal.")
    api_key, source, _project = row

    if api_key.revoked_at is not None:
        raise Unauthorized("API key sudah dicabut.")
    if api_key.key_hash != sha256_hex(secret):
        raise Unauthorized("API key tidak valid.")
    # API key is project scoped; the source must belong to the same project
    # AND the same org (defence in depth against a corrupted row).
    if source.id != source_id or source.org_id != api_key.org_id:
        raise Unauthorized("API key tidak berlaku untuk sumber ini.")

    info = ApiKeyInfo(
        key_id=api_key.id,
        org_id=api_key.org_id,
        project_id=api_key.project_id,
        source_id=source.id,
        source_type=source.type,
        prefix=api_key.prefix,
    )
    _cache_put(prefix, info, settings.API_KEY_CACHE_TTL_SECONDS)

    # Fire-and-forget usage timestamp: cheap single UPDATE, no flush needed
    # on the hot path beyond the normal commit.
    await session.execute(
        update(ApiKey).where(ApiKey.id == api_key.id).values(last_used_at=datetime.now(tz=timezone.utc))
    )
    return info


# ---------------------------------------------------------------------------
# 3. Source type gate
# ---------------------------------------------------------------------------
_INGEST_TYPES = {"webhook", "api"}


def ensure_ingestible(info: ApiKeyInfo) -> None:
    if info.source_type not in _INGEST_TYPES:
        metrics.ingest_rejected_total.labels(reason="source_not_ingestible").inc()
        raise Conflict(
            "Tipe sumber ini tidak menerima kiriman HTTP. Gunakan upload CSV."
        )


# ---------------------------------------------------------------------------
# 4. Payload parsing (light validation only)
# ---------------------------------------------------------------------------
def parse_payload(body: bytes) -> list[dict[str, Any]]:
    settings = get_settings()
    try:
        data = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        metrics.ingest_rejected_total.labels(reason="invalid_json").inc()
        raise ValidationProblem("Body harus berupa JSON yang valid.") from exc

    if isinstance(data, dict):
        rows: list[Any] = [data]
    elif isinstance(data, list):
        rows = data
    else:
        metrics.ingest_rejected_total.labels(reason="invalid_shape").inc()
        raise ValidationProblem(
            "Body harus berupa JSON object atau array of object."
        )

    if len(rows) > settings.INGEST_MAX_ROWS:
        metrics.ingest_rejected_total.labels(reason="too_many_rows").inc()
        raise PayloadTooLarge(
            f"Jumlah baris melebihi batas {settings.INGEST_MAX_ROWS} per permintaan."
        )

    for item in rows:
        if not isinstance(item, dict):
            metrics.ingest_rejected_total.labels(reason="non_object_row").inc()
            raise ValidationProblem("Setiap elemen array harus berupa object.")

    return rows


# ---------------------------------------------------------------------------
# 5. Idempotency (FR-016)
# ---------------------------------------------------------------------------
async def check_idempotency(
    session: AsyncSession, *, org_id: uuid.UUID, source_id: uuid.UUID, key: str | None
) -> IngestResult | None:
    """Return the previous result when this key was already processed."""
    if not key:
        return None
    row = (
        await session.execute(
            select(IngestIdempotency).where(
                IngestIdempotency.org_id == org_id,
                IngestIdempotency.source_id == source_id,
                IngestIdempotency.key == key,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        return None
    return IngestResult(
        accepted=int(row.response_accepted),
        batch_hint=row.response_batch_hint or get_settings().INGEST_BATCH_HINT,
        batch_id=str(row.id),
        replayed=True,
    )


async def store_idempotency(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    source_id: uuid.UUID,
    key: str,
    request_hash: str,
    accepted: int,
    batch_hint: str,
) -> None:
    session.add(
        IngestIdempotency(
            org_id=org_id,
            source_id=source_id,
            key=key,
            request_hash=request_hash,
            response_accepted=accepted,
            response_batch_hint=batch_hint,
        )
    )
    await session.flush()


# ---------------------------------------------------------------------------
# 6. Publish to the stream
# ---------------------------------------------------------------------------
async def publish_to_stream(redis: Any, stream: str, payload: dict[str, Any]) -> str:
    """Single pipelined XADD with an approximate MAXLEN bound."""
    settings = get_settings()
    entry_id = await redis.xadd(
        stream,
        payload,
        maxlen=settings.INGEST_MAX_STREAM_LEN,
        approximate=True,
    )
    return entry_id


def _entry_payload(
    rows: list[dict[str, Any]],
    info: ApiKeyInfo,
    batch_id: str,
    raw_body: bytes,
) -> dict[str, str]:
    """Redis entries are flat string maps; the rows go in one JSON field."""
    return {
        "batch_id": batch_id,
        "org_id": str(info.org_id),
        "project_id": str(info.project_id),
        "source_id": str(info.source_id),
        "received_at": datetime.now(tz=timezone.utc).isoformat(),
        "row_count": str(len(rows)),
        "rows": json.dumps(rows, separators=(",", ":"), default=str),
        "body_sha256": sha256_hex(raw_body.decode("utf-8", errors="ignore")),
    }


# ---------------------------------------------------------------------------
# Orchestration of the hot path
# ---------------------------------------------------------------------------
async def handle_ingest(
    *,
    session: AsyncSession,
    raw_key: str | None,
    source_id: uuid.UUID,
    content_length: int | None,
    raw_body: bytes,
    idempotency_key: str | None,
) -> IngestResult:
    """Run the full hot path. Raises ``AppError`` subclasses on rejection."""
    settings = get_settings()

    enforce_body_size(content_length)
    if len(raw_body) > settings.INGEST_MAX_BODY_BYTES:
        metrics.ingest_rejected_total.labels(reason="body_too_large").inc()
        raise PayloadTooLarge(
            f"Ukuran permintaan melebihi batas {settings.INGEST_MAX_BODY_BYTES} byte."
        )

    info = await authenticate_api_key(session, raw_key, source_id)
    ensure_ingestible(info)

    if not await ingest_rate_limit_ok(info.prefix):
        metrics.ingest_rejected_total.labels(reason="rate_limited").inc()
        raise TooManyRequests("Terlalu banyak permintaan.", retry_after=1)

    rows = parse_payload(raw_body)

    replayed = await check_idempotency(
        session, org_id=info.org_id, source_id=info.source_id, key=idempotency_key
    )
    if replayed is not None:
        metrics.ingest_requests_total.labels(status="202_replay").inc()
        return replayed

    stream = stream_key(str(info.org_id), str(info.source_id))
    await guard_backpressure(stream)

    batch_id = str(uuid.uuid4())
    try:
        await publish_to_stream(
            redis=get_redis(),
            stream=stream,
            payload=_entry_payload(rows, info, batch_id, raw_body),
        )
    except PayloadTooLarge:
        raise
    except TooManyRequests:
        raise
    except Exception as exc:  # Redis down: fail closed (NFR-03)
        metrics.ingest_rejected_total.labels(reason="redis_unavailable").inc()
        raise ServiceUnavailable(
            "Antrian ingest tidak tersedia. Data tidak diterima; silakan kirim ulang."
        ) from exc

    if idempotency_key:
        await store_idempotency(
            session,
            org_id=info.org_id,
            source_id=info.source_id,
            key=idempotency_key,
            request_hash=sha256_hex(batch_id),
            accepted=len(rows),
            batch_hint=settings.INGEST_BATCH_HINT,
        )
    # The first path already validated the key + source binding against the DB
    # in this transaction, so the cached copy can skip the re-check from now on.
    _cache_put(info.prefix, info, settings.API_KEY_CACHE_TTL_SECONDS)

    metrics.ingest_requests_total.labels(status="202").inc()
    metrics.ingest_rows_total.inc(len(rows))
    return IngestResult(
        accepted=len(rows),
        batch_hint=settings.INGEST_BATCH_HINT,
        batch_id=batch_id,
        replayed=False,
    )
