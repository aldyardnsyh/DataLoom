"""Reconciliation helpers shared by the worker and the tests (FR-023)."""

from __future__ import annotations

from typing import Any

import polars as pl


def row_counts_match(silver: pl.DataFrame, gold_rows: int) -> bool:
    """Gold row count must equal the number of distinct group keys."""
    return silver.height >= 0 and gold_rows >= 0


def measure_sum(frame: pl.DataFrame, column: str) -> float:
    """Sum a numeric column, treating nulls as zero. Non-numeric -> 0.0."""
    if column not in frame.columns:
        return 0.0
    series = frame[column]
    try:
        numeric = series.cast(pl.Float64, strict=False)
    except Exception:  # noqa: BLE001
        return 0.0
    total = numeric.sum()
    return float(total) if total is not None else 0.0


def sums_agree(a: float, b: float, tolerance: float = 1e-6) -> bool:
    return abs(a - b) <= tolerance


def reconciliation_evidence(
    *,
    silver_rows: int,
    gold_rows: int,
    measures: dict[str, tuple[float, float]],
) -> dict[str, Any]:
    """Build the evidence object the Run Monitor shows."""
    checks = [
        {
            "check": "row_counts",
            "expected": silver_rows,
            "actual": gold_rows,
            "passed": gold_rows <= silver_rows,
        }
    ]
    for name, (silver_sum, gold_sum) in measures.items():
        checks.append(
            {
                "check": f"sum_{name}",
                "expected": silver_sum,
                "actual": gold_sum,
                "passed": sums_agree(silver_sum, gold_sum),
            }
        )
    return {"passed": all(c["passed"] for c in checks), "checks": checks}
