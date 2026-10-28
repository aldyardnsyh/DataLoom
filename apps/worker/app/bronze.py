"""Bronze layer: immutable raw landing zone (FR-021).

Rules enforced here:
- Append only. The object key embeds ``batch_id`` so two batches can never
  collide, and nothing in this module exposes an update/delete path.
- Metadata columns are added WITHOUT touching the user payload:
  ``_source_id``, ``_received_at``, ``_batch_id``, ``_row_index``, ``_row_hash``.
- Partition layout: ``org/{org}/source/{source}/date=YYYY-MM-DD/part-*.parquet``.
"""

from __future__ import annotations

import hashlib
import io
import json
import uuid
from datetime import date, datetime, timezone
from typing import Any

import polars as pl

from app import storage

META_COLUMNS = [
    "_source_id",
    "_received_at",
    "_batch_id",
    "_row_index",
    "_row_hash",
]


def row_hash(row: dict[str, Any]) -> str:
    """Stable hash of a row's canonical JSON: the dedup key for Silver (FR-022)."""
    canonical = json.dumps(row, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def bronze_key(
    *, org_id: str, source_id: str, batch_id: str, partition_date: date
) -> str:
    """Deterministic object key. Same batch_id -> same key (retry safe)."""
    return (
        f"org/{org_id}/source/{source_id}/date={partition_date.isoformat()}"
        f"/part-{batch_id}.parquet"
    )


def build_dataframe(
    rows: list[dict[str, Any]],
    *,
    source_id: str,
    batch_id: str,
    received_at: datetime | None = None,
) -> pl.DataFrame:
    """Convert JSON rows to a Polars frame with metadata columns appended.

    Mixed-type columns are stringified to keep Bronze lossless and schema
    free: Bronze is "as received", the typed version happens in Silver.
    """
    if not rows:
        raise ValueError("Batch kosong tidak boleh ditulis ke Bronze.")

    received = received_at or datetime.now(tz=timezone.utc)

    # Union of keys keeps rows that are missing some fields (ragged JSON).
    columns: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for key in row.keys():
            if key not in seen:
                seen.add(key)
                columns.append(key)

    data: dict[str, list[Any]] = {c: [] for c in columns}
    for row in rows:
        for column in columns:
            value = row.get(column)
            if isinstance(value, (dict, list)):
                value = json.dumps(value, separators=(",", ":"), default=str)
            elif value is not None and not isinstance(value, (str, int, float, bool)):
                value = str(value)
            data[column].append(value)

    frame = pl.DataFrame(data, strict=False)

    # If a column ended up with mixed Python types Polars keeps it as Object.
    # Stringify those: Bronze must be storable in Parquet, typing is Silver's job.
    for column in frame.columns:
        if frame.schema[column] == pl.Object:
            frame = frame.with_columns(
                pl.col(column)
                .map_elements(lambda v: None if v is None else str(v), return_dtype=pl.String)
                .alias(column)
            )

    frame = frame.with_columns(
        [
            pl.lit(source_id).alias("_source_id"),
            pl.lit(received.isoformat()).alias("_received_at"),
            pl.lit(batch_id).alias("_batch_id"),
            pl.int_range(0, frame.height, eager=True).alias("_row_index"),
        ]
    )
    hashes = [row_hash(r) for r in rows]
    frame = frame.with_columns(pl.Series("_row_hash", hashes))
    return frame


def write_bronze(
    frame: pl.DataFrame, *, org_id: str, source_id: str, batch_id: str
) -> tuple[str, int]:
    """Serialise to Parquet and upload. Returns ``(uri, rows_written)``."""
    partition = datetime.now(tz=timezone.utc).date()
    key = bronze_key(
        org_id=org_id, source_id=source_id, batch_id=batch_id, partition_date=partition
    )

    buffer = io.BytesIO()
    frame.write_parquet(buffer, compression="zstd")
    payload = buffer.getvalue()

    # Integrity guard: never publish a Parquet blob we cannot read back.
    check = pl.read_parquet(io.BytesIO(payload))
    if check.height != frame.height:
        raise RuntimeError("Verifikasi Parquet gagal: jumlah baris tidak cocok.")

    storage.put_bytes(key, payload, "application/vnd.apache.parquet")
    return storage.uri_for(key), frame.height


def read_bronze(uri: str) -> pl.DataFrame:
    """Read a Bronze object back. Used by Silver and by replays."""
    return pl.read_parquet(io.BytesIO(storage.get_bytes(uri)))


def dataset_schema(uri: str) -> list[dict[str, str]]:
    """Describe columns for ``datasets.schema_json`` (Bronze is schema-loose)."""
    frame = read_bronze(uri)
    return [{"name": name, "dtype": str(dtype)} for name, dtype in frame.schema.items()]


__all__ = [
    "META_COLUMNS",
    "bronze_key",
    "build_dataframe",
    "dataset_schema",
    "read_bronze",
    "row_hash",
    "write_bronze",
]
