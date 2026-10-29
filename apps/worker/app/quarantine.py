"""Quarantine layer (FR-022).

Every row rejected by a validation rule lands here WITH a reason code, never
silently dropped. The quarantine dataset is replayable: fixing a rule and
re-running re-reads Bronze, it never needs these rows back.
"""

from __future__ import annotations

import io
from datetime import datetime, timezone
from typing import Any

import polars as pl

from app import storage

REASON_COLUMNS = ["_reason_code", "_reason_detail", "_rule_id", "_quarantined_at"]


def quarantine_key(*, org_id: str, run_id: str, node_id: str) -> str:
    stamp = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%S%f")
    return f"org/{org_id}/quarantine/run={run_id}/{node_id}-{stamp}.parquet"


def build_quarantine_frame(
    rows: list[dict[str, Any]],
    *,
    reason_code: str,
    reason_detail: str,
    rule_id: str | None = None,
) -> pl.DataFrame:
    """Wrap rejected rows with the machine-readable rejection reason."""
    frame = pl.DataFrame(rows, strict=False) if rows else pl.DataFrame()
    if frame.height == 0:
        return frame
    # ``silver.apply_rules`` already annotates each rejected row with the
    # specific rule code and rule id. Overwriting those columns with a generic
    # "validation_failed" would destroy the only information an operator needs
    # to fix the rule, so keep the existing annotation when it is present.
    if "_reason_code" in frame.columns:
        return frame
    return frame.with_columns(
        [
            pl.lit(reason_code).alias("_reason_code"),
            pl.lit(reason_detail[:1000]).alias("_reason_detail"),
            pl.lit(rule_id or "").alias("_rule_id"),
            pl.lit(datetime.now(tz=timezone.utc).isoformat()).alias("_quarantined_at"),
        ]
    )


def write_quarantine(
    frame: pl.DataFrame, *, org_id: str, run_id: str, node_id: str
) -> tuple[str, int] | None:
    """Persist rejected rows. Returns ``None`` when there is nothing to write."""
    if frame.height == 0:
        return None
    buffer = io.BytesIO()
    frame.write_parquet(buffer, compression="zstd")
    key = quarantine_key(org_id=org_id, run_id=run_id, node_id=node_id)
    storage.put_bytes(key, buffer.getvalue(), "application/vnd.apache.parquet")
    return storage.uri_for(key), frame.height


def reason_counts(frame: pl.DataFrame) -> dict[str, int]:
    """Aggregate counts per reason code, used by the Quality tab and the gate."""
    if frame.height == 0 or "_reason_code" not in frame.columns:
        return {}
    grouped = frame.group_by("_reason_code").len()
    return {str(r["_reason_code"]): int(r["len"]) for r in grouped.to_dicts()}
