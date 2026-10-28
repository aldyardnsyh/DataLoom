"""Deterministic column statistics for the Schema Profiler (PRD A1 input).

**No LLM touches this module.** Every number the profiler prompt shows is
computed here from a Polars frame, so a hallucinated statistic cannot enter a
report: the report's ``ColumnProfile.stats`` is built from these dicts, and the
LLM only chooses dtype/semantic/anomalies/proposals on top of them.

Design constraints
------------------
- Pure functions over an in-memory frame; no I/O, no DB, no network.
- Bounded work: sample values capped at 50, pattern detection scans at most
  :data:`PATTERN_SCAN_ROWS` non-null values per column.
- Regexes used here are owned by this module and are linear (no nested
  quantifiers, no backreferences) so a pathological input cannot blow up.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, time
from typing import Any

import polars as pl

#: Column metadata columns written by Bronze/Silver; never profiled as business data.
META_COLUMNS: frozenset[str] = frozenset(
    {"_source_id", "_received_at", "_batch_id", "_row_index", "_row_hash"}
)

#: How many non-null raw values inspect per column, sorted by value. Sorting
#: makes the sample deterministic across runs (Polars' own sampling is not).
RAW_SCAN_ROWS = 200

#: Hard cap on how many values a pattern regex is evaluated against.
PATTERN_SCAN_ROWS = 200

#: Token used when the frame has zero rows: we cannot invent a row count.
NULL_TOKENS: frozenset[str] = frozenset({"", "null", "none", "nan", "na", "n/a", "-", "nil"})


# ---------------------------------------------------------------------------
# Pattern catalogue (linear regexes only)
# ---------------------------------------------------------------------------
@dataclass(slots=True, frozen=True)
class PatternSpec:
    name: str
    regex: re.Pattern[str]
    semantic_hint: str


PATTERNS: tuple[PatternSpec, ...] = (
    PatternSpec("iso_date", re.compile(r"^\d{4}-\d{2}-\d{2}$"), "date"),
    PatternSpec("ddmmyyyy", re.compile(r"^\d{1,2}[/-]\d{1,2}[/-]\d{4}$"), "date"),
    PatternSpec("iso_timestamp", re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2})?$"), "date"),
    PatternSpec("email", re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$"), "email"),
    PatternSpec("phone_intl", re.compile(r"^\+[1-9]\d{6,14}$"), "phone"),
    PatternSpec("phone_local", re.compile(r"^0\d{8,13}$"), "phone"),
    PatternSpec("uuid", re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"), "identifier"),
    PatternSpec("integer_text", re.compile(r"^-?\d+$"), "integer"),
    PatternSpec("decimal_comma", re.compile(r"^-?\d+,\d+$"), "currency"),
    PatternSpec("decimal_dot", re.compile(r"^-?\d+\.\d+$"), "float"),
    PatternSpec("currency_prefix", re.compile(r"^(Rp|IDR|USD|\$)\s?[\d.,]+$"), "currency"),
    PatternSpec("numeric_with_sep", re.compile(r"^-?\d{1,3}([.,]\d{3})+$"), "currency"),
)

#: A pattern must cover at least this share of scanned values to be reported.
PATTERN_MIN_COVERAGE = 0.8


# ---------------------------------------------------------------------------
# Output shape
# ---------------------------------------------------------------------------
@dataclass(slots=True)
class ColumnStatsResult:
    """One column's deterministic statistics.

    Converted into ``dataloom_contracts.agents.ColumnStats`` by the profiler
    graph (``app.graphs.profiler.load_stats``); kept as a plain dataclass here
    so this module has no dependency on the agent graphs.
    """

    name: str
    polars_dtype: str
    row_count: int
    null_count: int
    null_ratio: float
    distinct_count: int | None
    min_value: str | None = None
    max_value: str | None = None
    mean_value: float | None = None
    sample_values: list[str] = field(default_factory=list)
    top_patterns: list[str] = field(default_factory=list)
    #: Extra deterministic facts the prompt may use but the contract has no
    #: field for (kept out of ``ColumnStats`` on purpose: extra="forbid").
    extras: dict[str, Any] = field(default_factory=dict)

    def to_contract_dict(self) -> dict[str, Any]:
        """Project onto ``dataloom_contracts.agents.ColumnStats`` fields."""
        return {
            "row_count": self.row_count,
            "null_count": self.null_count,
            "null_ratio": round(self.null_ratio, 6),
            "distinct_count": self.distinct_count,
            "min_value": self.min_value,
            "max_value": self.max_value,
            "mean_value": self.mean_value,
            # ColumnStats allows max 50 sample values.
            "sample_values": self.sample_values[:50],
            "top_patterns": self.top_patterns,
        }

    def as_prompt_dict(self) -> dict[str, Any]:
        """Prompt-facing projection.

        Superset of :meth:`to_contract_dict`: adds ``name``, ``dtype``, the
        deterministic ``patterns`` map (pattern -> match ratio) and examples,
        so the profiler can build an evidence sentence from real numbers
        instead of trusting the model's own arithmetic.
        """
        payload: dict[str, Any] = {"name": self.name, "dtype": self.polars_dtype}
        payload.update(self.to_contract_dict())
        payload["patterns"] = dict(self.extras.get("pattern_coverage") or {})
        payload["examples"] = self.sample_values[:5]
        return payload


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def business_columns(frame: pl.DataFrame) -> list[str]:
    """Column names that represent user data (metadata columns removed)."""
    return [c for c in frame.columns if c not in META_COLUMNS]


def _stringify(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    return str(value)


def _distinct_text_values(series: pl.Series, limit: int) -> list[str]:
    """Deterministic sample: distinct non-null values, sorted, capped.

    Sorting keeps the sample stable between runs; ``limit`` keeps prompt size
    bounded (PRD A1: ``sample_rows`` max 50).
    """
    try:
        unique = series.drop_nulls().unique().sort()
    except Exception:  # noqa: BLE001 - mixed types cannot be sorted
        unique = series.drop_nulls().unique()
    values: list[str] = []
    for item in unique.head(limit).to_list():
        text = _stringify(item)
        if text is not None:
            values.append(text)
    return values


def _detect_patterns(values: list[str]) -> tuple[list[str], dict[str, float]]:
    """Return pattern names covering >= ``PATTERN_MIN_COVERAGE`` of values.

    Also returns the per-pattern coverage so the prompt can show a percentage
    (the PRD's example proposal quotes "97,2% nilai cocok pola").
    """
    scanned = [v for v in values if v not in NULL_TOKENS][:PATTERN_SCAN_ROWS]
    if not scanned:
        return [], {}
    coverage: dict[str, float] = {}
    for spec in PATTERNS:
        hits = sum(1 for value in scanned if spec.regex.match(value))
        ratio = hits / len(scanned)
        if ratio >= PATTERN_MIN_COVERAGE:
            coverage[spec.name] = round(ratio, 4)
    # Report the strongest patterns first; ties broken by catalogue order.
    ordered = sorted(coverage.items(), key=lambda kv: (-kv[1], kv[0]))
    return [name for name, _ in ordered], dict(ordered)


def _numeric_summary(series: pl.Series) -> dict[str, Any]:
    numeric = series.cast(pl.Float64, strict=False)
    non_null = numeric.drop_nulls()
    if non_null.len() == 0:
        return {}
    try:
        computed_median = float(non_null.median())  # type: ignore[arg-type]
        return {
            "mean": float(round(float(non_null.mean() or 0.0), 6)),  # type: ignore[arg-type]
            "std": float(round(float(non_null.std() or 0.0), 6)),  # type: ignore[arg-type]
            "median": computed_median,
            "min": float(non_null.min()),  # type: ignore[arg-type]
            "max": float(non_null.max()),  # type: ignore[arg-type]
            "negative_count": int((non_null < 0).sum()),
            "zero_count": int((non_null == 0).sum()),
        }
    except (TypeError, ValueError):
        return {}


def _text_ratio_in_tokens(series: pl.Series) -> float:
    """Share of string values that are common null tokens (for null_spike evidence)."""
    text = series.cast(pl.String, strict=False).drop_nulls()
    if text.len() == 0:
        return 0.0
    masked = text.str.to_lowercase().str.strip_chars().is_in(list(NULL_TOKENS))
    return float(masked.sum()) / float(text.len())


def _length_summary(series: pl.Series) -> dict[str, Any]:
    text = series.cast(pl.String, strict=False).drop_nulls()
    if text.len() == 0:
        return {}
    lengths = text.str.len_chars()
    return {
        "min_length": int(lengths.min() or 0),  # type: ignore[arg-type]
        "max_length": int(lengths.max() or 0),  # type: ignore[arg-type]
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def profile_column(series: pl.Series, *, total_rows: int | None = None, sample_limit: int = 50) -> ColumnStatsResult:
    """Compute every deterministic statistic for one column."""
    rows = int(total_rows if total_rows is not None else series.len())
    non_null = int(series.count())
    null_count = max(0, rows - non_null)
    null_ratio = (null_count / rows) if rows else 0.0

    distinct: int | None
    try:
        distinct = int(series.n_unique()) - (1 if series.null_count() else 0)
        distinct = max(0, distinct)
    except Exception:  # noqa: BLE001 - e.g. unhashable object dtype
        distinct = None

    samples = _distinct_text_values(series, sample_limit)
    pattern_names, pattern_coverage = _detect_patterns(_distinct_text_values(series, RAW_SCAN_ROWS))

    extras: dict[str, Any] = {
        "pattern_coverage": pattern_coverage,
        "null_token_ratio": round(_text_ratio_in_tokens(series), 6),
    }

    min_value: str | None = None
    max_value: str | None = None
    mean_value: float | None = None

    dtype = series.dtype
    is_numeric = dtype.is_numeric() or dtype == pl.Boolean
    if is_numeric:
        summary = _numeric_summary(series)
        extras["numeric"] = summary
        if summary:
            min_value = f"{summary['min']:g}"
            max_value = f"{summary['max']:g}"
            mean_value = summary["mean"]
    else:
        extras["lengths"] = _length_summary(series)
        for cast_dtype, label in ((pl.Date, "date"), (pl.Datetime, "timestamp")):
            try:
                casted = series.cast(cast_dtype, strict=False).drop_nulls()
            except Exception:  # noqa: BLE001
                continue
            if casted.len() and casted.len() == non_null:
                extras["temporal"] = {
                    "kind": label,
                    "min": str(casted.min()),
                    "max": str(casted.max()),
                }
                min_value = str(casted.min())
                max_value = str(casted.max())
                break
        if min_value is None and non_null:
            # Lexicographic bounds for text: honest, and labelled as such.
            text = series.cast(pl.String, strict=False).drop_nulls()
            if text.len():
                extras["lexicographic_bounds"] = True
                min_value = str(text.min())[:120]
                max_value = str(text.max())[:120]

    return ColumnStatsResult(
        name=series.name,
        polars_dtype=str(dtype),
        row_count=rows,
        null_count=null_count,
        null_ratio=null_ratio,
        distinct_count=distinct,
        min_value=min_value,
        max_value=max_value,
        mean_value=mean_value,
        sample_values=samples[: max(0, min(50, sample_limit))],
        top_patterns=pattern_names,
        extras=extras,
    )


def profile_frame(frame: pl.DataFrame, *, sample_limit: int = 50) -> list[ColumnStatsResult]:
    """Profile every business column of ``frame`` (deterministic order)."""
    total = frame.height
    return [
        profile_column(frame[name], total_rows=total, sample_limit=sample_limit)
        for name in business_columns(frame)
    ]


def duplicate_row_count(frame: pl.DataFrame) -> int:
    """Number of rows that are exact duplicates of an earlier row.

    Uses the business columns only, so two batches differing solely in
    ``_batch_id`` are still recognised as duplicate *data*.
    """
    columns = business_columns(frame)
    if frame.height == 0 or not columns:
        return 0
    deduped = frame.select(columns).unique(maintain_order=True)
    return int(frame.height - deduped.height)


def mixed_type_ratio(series: pl.Series) -> float:
    """Share of values that are neither numeric-like nor obviously textual.

    Deterministic ``type_mix`` evidence: a column whose text values match
    ``integer_text`` for 90% of rows but also contains 10% prose will report
    ``0.1``.
    """
    values = _distinct_text_values(series, RAW_SCAN_ROWS)
    if not values:
        return 0.0
    numericish = sum(
        1 for v in values if re.match(r"^-?\d+([.,]\d+)?$", v) or v.lower() in {"true", "false"}
    )
    return round(1.0 - (numericish / len(values)), 6)


__all__ = [
    "ColumnStatsResult",
    "META_COLUMNS",
    "PATTERNS",
    "PATTERN_MIN_COVERAGE",
    "business_columns",
    "duplicate_row_count",
    "mixed_type_ratio",
    "profile_column",
    "profile_frame",
]


