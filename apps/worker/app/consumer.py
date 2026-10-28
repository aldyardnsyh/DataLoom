"""Redis Streams consumer with at-least-once delivery (FR-021, NFR-08).

Guarantees:
- ``XACK`` happens ONLY after the Bronze object is durably written.
- Unacked entries stay in the consumer group Pending Entries List and are
  re-claimed by any live consumer after ``CLAIM_IDLE_MS`` (restart recovery).
- Duplicate delivery is harmless: dedup at Silver uses ``_row_hash``.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import polars as pl
import redis.asyncio as aioredis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import bronze, metrics
from app.config import get_settings
from app.db import Dataset, Source

logger = logging.getLogger("dataloom.worker.consumer")


@dataclass(slots=True)
class BatchEntry:
    entry_id: str
    org_id: str
    project_id: str
    source_id: str
    batch_id: str
    rows: list[dict[str, Any]] = field(default_factory=list)


def decode_entry(entry_id: str, fields: dict[str, str]) -> BatchEntry | None:
    """Decode one stream entry. Malformed entries are acked and counted, never
    retried forever (a poison message must not block the stream)."""
    try:
        rows = json.loads(fields.get("rows", "[]"))
        if not isinstance(rows, list):
            raise ValueError("rows bukan list")
    except (ValueError, json.JSONDecodeError):
        metrics.ingest_rejected_total.labels(reason="malformed_stream_entry").inc()
        logger.warning("Melewati entri stream rusak %s", entry_id)
        return None

    return BatchEntry(
        entry_id=entry_id,
        org_id=fields["org_id"],
        project_id=fields["project_id"],
        source_id=fields["source_id"],
        batch_id=fields.get("batch_id", str(uuid.uuid4())),
        rows=rows,
    )


async def list_streams(redis: aioredis.Redis) -> list[str]:
    """All ingest streams (``ingest:{org}:{source}``)."""
    streams: list[str] = []
    cursor = 0
    while True:
        cursor, keys = await redis.scan(cursor=cursor, match="ingest:*", count=200)
        streams.extend(keys)
        if cursor == 0:
            break
    return streams


async def ensure_group(redis: aioredis.Redis, stream: str, group: str) -> None:
    """Create the consumer group at the beginning of the stream."""
    try:
        await redis.xgroup_create(stream, group, id="0", mkstream=True)
    except Exception as exc:  # noqa: BLE001 - BUSYGROUP means it already exists
        if "BUSYGROUP" not in str(exc):
            raise


async def read_new(
    redis: aioredis.Redis, stream: str, group: str, consumer: str, count: int, block_ms: int
) -> list[tuple[str, dict[str, str]]]:
    """Read new (undelivered) entries via XREADGROUP ``>``."""
    try:
        response = await redis.xreadgroup(
            group, consumer, {stream: ">"}, count=count, block=block_ms
        )
    except Exception:  # noqa: BLE001 - timeout returns empty on some clients
        return []
    if not response:
        return []
    entries: list[tuple[str, dict[str, str]]] = []
    for _stream, items in response:
        entries.extend(items)
    return entries


async def claim_stale(
    redis: aioredis.Redis, stream: str, group: str, consumer: str, min_idle_ms: int
) -> list[tuple[str, dict[str, str]]]:
    """Claim entries left pending by a dead consumer (restart recovery)."""
    try:
        pending = await redis.xautoclaim(
            stream, group, consumer, min_idle_time=min_idle_ms, start_id="0-0", count=100
        )
    except Exception:  # noqa: BLE001
        return []
    # xautoclaim returns [next_cursor, entries, deleted]
    if not pending or len(pending) < 2:
        return []
    claimed = pending[1] or []
    return [(eid, fields) for eid, fields in claimed if fields]


async def ack(redis: aioredis.Redis, stream: str, group: str, *entry_ids: str) -> None:
    if entry_ids:
        await redis.xack(stream, group, *entry_ids)


# ---------------------------------------------------------------------------
# Bronze persistence
# ---------------------------------------------------------------------------
async def persist_bronze(
    session: AsyncSession, entries: list[BatchEntry]
) -> dict[str, int]:
    """Write one Parquet object per (org, source, batch) group.

    Entries from different sources are NEVER mixed in one object: a tenant
    partition must not be shareable, and partition pruning stays correct.
    """
    grouped: dict[tuple[str, str, str], list[BatchEntry]] = {}
    for entry in entries:
        grouped.setdefault((entry.org_id, entry.source_id, entry.batch_id), []).append(entry)

    written: dict[str, int] = {}
    for (org_id, source_id, batch_id), group in grouped.items():
        rows: list[dict[str, Any]] = []
        for entry in group:
            rows.extend(entry.rows)
        if not rows:
            continue

        frame = bronze.build_dataframe(rows, source_id=source_id, batch_id=batch_id)
        uri, count = bronze.write_bronze(
            frame, org_id=org_id, source_id=source_id, batch_id=batch_id
        )

        # Dataset bookkeeping: one bronze dataset per source.
        dataset = (
            await session.execute(
                select(Dataset).where(
                    Dataset.org_id == uuid.UUID(org_id),
                    Dataset.source_id == uuid.UUID(source_id),
                    Dataset.layer == "bronze",
                )
            )
        ).scalar_one_or_none()
        if dataset is None:
            dataset = Dataset(
                org_id=uuid.UUID(org_id),
                project_id=uuid.UUID(group[0].project_id),
                source_id=uuid.UUID(source_id),
                layer="bronze",
                name=f"bronze_{source_id[:8]}",
                schema_json={},
                storage_uri=uri,
                row_count=0,
            )
            session.add(dataset)
        dataset.storage_uri = uri
        dataset.row_count = int(dataset.row_count or 0) + count
        dataset.updated_at = datetime.now(tz=timezone.utc)

        source = (
            await session.execute(
                select(Source).where(
                    Source.id == uuid.UUID(source_id), Source.org_id == uuid.UUID(org_id)
                )
            )
        ).scalar_one_or_none()
        if source is not None:
            source.last_received_at = datetime.now(tz=timezone.utc)
            source.status = "connected"

        await session.flush()
        metrics.worker_rows_total.labels(layer="bronze").inc(count)
        written[batch_id] = count

    return written
