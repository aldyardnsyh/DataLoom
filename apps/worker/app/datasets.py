"""Dataset registry writes for Silver, Gold, and Quarantine.

The API reads ``datasets`` rows to build the Data Explorer. Bronze rows are
created by the ingest path; the worker owns the other three layers. Keeping the
upsert here means a run either publishes a readable dataset row or fails before
the row is committed, never a row that points at a missing object.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from typing import Any

import polars as pl
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import Dataset


def safe_dataset_name(value: str, *, fallback: str = "dataset") -> str:
    """Lower-case bare identifier, compatible with Dataset.name and SQL use."""
    slug = re.sub(r"[^a-z0-9_]+", "_", str(value or "").strip().lower()).strip("_")
    return (slug or fallback)[:128]


def frame_columns(frame: pl.DataFrame | None) -> list[dict[str, str]]:
    if frame is None:
        return []
    return [{"name": str(name), "dtype": str(dtype)} for name, dtype in frame.schema.items()]


async def register_dataset(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    layer: str,
    name: str,
    storage_uri: str,
    row_count: int,
    source_id: uuid.UUID | None = None,
    frame: pl.DataFrame | None = None,
    columns: list[dict[str, str]] | None = None,
    extra: dict[str, Any] | None = None,
) -> Dataset:
    """Upsert one dataset row by ``(org, project, layer, name)``.

    The latest object for a logical dataset wins. Runs keep their own Parquet
    objects, so history is not lost at the storage layer; the registry points
    at the newest readable snapshot, which is what the Explorer should show.
    """
    safe_name = safe_dataset_name(name)
    schema_json: dict[str, Any] = dict(extra or {})
    resolved_columns = columns if columns is not None else frame_columns(frame)
    if resolved_columns:
        schema_json["columns"] = resolved_columns

    row = (
        await session.execute(
            select(Dataset).where(
                Dataset.org_id == org_id,
                Dataset.project_id == project_id,
                Dataset.layer == layer,
                Dataset.name == safe_name,
            )
        )
    ).scalar_one_or_none()

    now = datetime.now(tz=timezone.utc)
    if row is None:
        row = Dataset(
            org_id=org_id,
            project_id=project_id,
            source_id=source_id,
            layer=layer,
            name=safe_name,
            schema_json=schema_json,
            storage_uri=storage_uri,
            row_count=int(row_count),
        )
        session.add(row)
    else:
        row.source_id = source_id
        row.schema_json = schema_json
        row.storage_uri = storage_uri
        row.row_count = int(row_count)
        row.updated_at = now

    await session.flush()
    return row