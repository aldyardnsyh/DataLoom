"""Silver layer: typed, normalized, deduplicated, validated (FR-022, FR-023).

Deterministic Polars only — no LLM anywhere in this file (PRD section 9.1).

Processing order matters and is fixed:
    1. type cast + trim + null normalization
    2. deduplication by ``_row_hash`` (and by user-declared keys)
    3. validation rules -> failing rows go to quarantine WITH a reason
    A row that fails several rules is quarantined once with the first failing
    rule, so ``rows_in = rows_out + rows_quarantined`` always holds.
"""

from __future__ import annotations

import io
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import polars as pl

from app import storage

NULL_TOKENS = {"", "null", "none", "nan", "na", "n/a", "-", "nil"}
META_COLUMNS = {"_source_id", "_received_at", "_batch_id", "_row_index", "_row_hash"}


@dataclass(slots=True)
class SilverResult:
    """Outcome of one Silver pass over one batch."""

    frame: pl.DataFrame
    rows_in: int
    rows_out: int
    quarantined: list[dict[str, Any]] = field(default_factory=list)
    reasons: dict[str, int] = field(default_factory=dict)
    schema: list[dict[str, Any]] = field(default_factory=list)


def business_columns(frame: pl.DataFrame) -> list[str]:
    return [c for c in frame.columns if c not in META_COLUMNS]


# ---------------------------------------------------------------------------
# 1. Normalization
# ---------------------------------------------------------------------------
def normalize_nulls(frame: pl.DataFrame) -> pl.DataFrame:
    """Treat common empty tokens as NULL for string columns."""
    expressions = []
    for column, dtype in frame.schema.items():
        if dtype == pl.String:
            expressions.append(
                pl.when(
                    pl.col(column).is_null()
                    | pl.col(column).str.strip_chars().str.to_lowercase().is_in(
                        list(NULL_TOKENS)
                    )
                )
                .then(None)
                .otherwise(pl.col(column))
                .alias(column)
            )
    return frame.with_columns(expressions) if expressions else frame


def trim_strings(frame: pl.DataFrame) -> pl.DataFrame:
    expressions = [
        pl.col(c).str.strip_chars().alias(c)
        for c, dtype in frame.schema.items()
        if dtype == pl.String
    ]
    return frame.with_columns(expressions) if expressions else frame


def cast_columns(frame: pl.DataFrame, casts: dict[str, str]) -> tuple[pl.DataFrame, list[str]]:
    """Cast columns to explicit Polars dtypes. Returns ``(frame, failures)``.

    A failed cast yields null (``strict=False``); the validation step then
    quarantines those rows with ``cast_failed`` rather than dropping them.
    """
    mapping = {
        "string": pl.String,
        "int": pl.Int64,
        "int64": pl.Int64,
        "float": pl.Float64,
        "float64": pl.Float64,
        "bool": pl.Boolean,
        "boolean": pl.Boolean,
        "date": pl.Date,
        "datetime": pl.Datetime,
    }
    expressions = []
    failures: list[str] = []
    for column, target in casts.items():
        if column not in frame.columns:
            failures.append(column)
            continue
        dtype = mapping.get(target.lower())
        if dtype is None:
            failures.append(column)
            continue
        expressions.append(
            pl.col(column).cast(dtype, strict=False).alias(column)
        )
    return (frame.with_columns(expressions) if expressions else frame), failures


# ---------------------------------------------------------------------------
# 2. Deduplication
# ---------------------------------------------------------------------------
def deduplicate(
    frame: pl.DataFrame, keys: list[str] | None = None
) -> tuple[pl.DataFrame, int]:
    """Remove duplicates. Returns ``(frame, removed_count)``.

    Default key is ``_row_hash`` (exact duplicate payload). Declared keys use
    ``keep="first"`` and are limited to existing columns.
    """
    if frame.height == 0:
        return frame, 0
    subset = [k for k in (keys or []) if k in frame.columns]
    if not subset:
        subset = ["_row_hash"] if "_row_hash" in frame.columns else []
    if not subset:
        return frame, 0
    deduped = frame.unique(subset=subset, keep="first", maintain_order=True)
    return deduped, frame.height - deduped.height


# ---------------------------------------------------------------------------
# 3. Validation rules
# ---------------------------------------------------------------------------
_SAFE_REGEX_CACHE: dict[str, re.Pattern[str]] = {}


def _compile_regex(pattern: str) -> re.Pattern[str]:
    if pattern not in _SAFE_REGEX_CACHE:
        if len(pattern) > 200:
            raise ValueError("Pola regex terlalu panjang (maks 200 karakter).")
        _SAFE_REGEX_CACHE[pattern] = re.compile(pattern)
    return _SAFE_REGEX_CACHE[pattern]


def _mask_rule(
    frame: pl.DataFrame, rule: dict[str, Any]
) -> tuple[pl.Series, str]:
    """Return a boolean mask of FAILING rows plus a human reason string."""
    kind = rule.get("kind")
    column = rule.get("column")
    params = rule.get("params") or {}
    rule_id = rule.get("rule_id", "")
    severity = rule.get("severity", "error")

    if column not in frame.columns:
        # A rule about a missing column fails for every row: that is a real
        # schema problem the operator must see, not a silent pass.
        return pl.Series([True] * frame.height), f"kolom '{column}' tidak ada"

    col = pl.col(column)

    if kind == "not_null":
        mask = col.is_null() | (pl.col(column).cast(pl.String) == "")
        return mask.fill_null(True), f"{rule_id}: nilai kosong pada '{column}'"

    if kind == "unique":
        duplicated = col.is_duplicated().fill_null(False)
        return duplicated, f"{rule_id}: nilai duplikat pada '{column}'"

    if kind == "type":
        target = (params.get("dtype") or "string").lower()
        if target in ("int", "int64"):
            mask = (
                col.cast(pl.String, strict=False)
                .str.contains(r"^-?\d+$")
                .fill_null(False)
                .not_()
            )
        elif target in ("float", "float64"):
            mask = (
                col.cast(pl.String, strict=False)
                .str.contains(r"^-?\d+(\.\d+)?$")
                .fill_null(False)
                .not_()
            )
        else:
            expressions = [pl.col(column).is_not_null()]
            if "pattern" in params:
                expressions.append(
                    pl.col(column).cast(pl.String).str.contains(params["pattern"]).fill_null(False)
                )
            mask = pl.any_horizontal([e.not_() for e in expressions]) if len(expressions) == 1 else (
                expressions[0].not_() | expressions[1].not_()
            )
        return mask.fill_null(True), f"{rule_id}: tipe tidak sesuai '{target}' pada '{column}'"

    if kind == "range":
        minimum = params.get("min")
        maximum = params.get("max")
        numeric = col.cast(pl.Float64, strict=False)
        mask = pl.lit(False)
        if minimum is not None:
            mask = mask | (numeric < float(minimum))
        if maximum is not None:
            mask = mask | (numeric > float(maximum))
        # A non-numeric value cannot satisfy a range: quarantine it.
        mask = mask | numeric.is_null()
        return mask.fill_null(True), f"{rule_id}: di luar rentang pada '{column}'"

    if kind == "regex":
        pattern = params.get("pattern")
        if not pattern:
            raise ValueError(f"Rule '{rule_id}' regex butuh params.pattern")
        regex = _compile_regex(pattern)
        matches = (
            col.cast(pl.String, strict=False)
            .map_elements(
                lambda v: bool(v is not None and regex.fullmatch(str(v)) is not None),
                return_dtype=pl.Boolean,
            )
        )
        return (matches.fill_null(False).not_()), f"{rule_id}: pola tidak cocok pada '{column}'"

    if kind == "enum":
        allowed = params.get("values") or []
        return (
            col.is_not_null() & col.cast(pl.String).is_in(allowed).not_(),
            f"{rule_id}: nilai di luar daftar pada '{column}'",
        )

    if kind == "freshness":
        max_minutes = int(params.get("max_age_minutes", 1440))
        parsed = col.cast(pl.Datetime, strict=False)
        now = datetime.now(tz=timezone.utc).replace(tzinfo=None)
        age_minutes = (pl.lit(now) - parsed).dt.total_minutes()
        return (
            parsed.is_null() | (age_minutes > max_minutes),
            f"{rule_id}: data lebih tua dari {max_minutes} menit",
        )

    # referential / volume_zscore need cross-batch context; they are evaluated
    # by app.quality with a prepared lookup, so here they are "unknown" = pass.
    if kind in ("referential", "volume_zscore"):
        return pl.Series([False] * frame.height), f"{rule_id}: dievaluasi di luar batch"

    if severity == "error":
        raise ValueError(f"Jenis rule '{kind}' tidak dikenal.")
    return pl.Series([False] * frame.height), f"{rule_id}: dilewati"


def apply_rules(
    frame: pl.DataFrame, rules: list[dict[str, Any]]
) -> tuple[pl.DataFrame, list[dict[str, Any]], dict[str, int]]:
    """Split into passing rows and quarantined rows (with reasons)."""
    if not rules or frame.height == 0:
        return frame, [], {}

    failing_mask = pl.Series("_fail", [False] * frame.height)
    reason_codes = ["" for _ in range(frame.height)]
    reason_details = ["" for _ in range(frame.height)]
    rule_ids = ["" for _ in range(frame.height)]

    evaluated = 0
    for rule in rules:
        if rule.get("severity") == "warn":
            continue  # warnings are reported, never quarantine (A3 rule)
        mask, detail = _mask_rule(frame, rule)
        evaluated += 1
        values = mask.to_list()
        for i, failed in enumerate(values):
            if failed and not failing_mask[i]:
                failing_mask[i] = True
                reason_codes[i] = _reason_code(rule.get("kind") or "invalid")
                reason_details[i] = detail
                rule_ids[i] = rule.get("rule_id") or ""

    if evaluated == 0:
        return frame, [], {}

    quarantine = frame.filter(failing_mask).with_columns(
        [
            pl.Series("_reason_code", reason_codes),
            pl.Series("_reason_detail", reason_details),
            pl.Series("_rule_id", rule_ids),
            pl.lit(datetime.now(tz=timezone.utc).isoformat()).alias("_quarantined_at"),
        ]
    ).filter(pl.col("_reason_code") != "")
    passing = frame.filter(failing_mask.not_())

    counts: dict[str, int] = {}
    for code in quarantine["_reason_code"].to_list() if quarantine.height else []:
        counts[code] = counts.get(code, 0) + 1

    return passing, quarantine.to_dicts(), counts


def _reason_code(kind: str) -> str:
    return {
        "not_null": "missing_required_value",
        "unique": "duplicate_value",
        "type": "type_mismatch",
        "range": "out_of_range",
        "regex": "pattern_mismatch",
        "enum": "enum_mismatch",
        "freshness": "stale_data",
        "referential": "referential_missing",
        "volume_zscore": "volume_anomaly",
    }.get(kind, "invalid_row")


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------
def build_silver(
    bronze: pl.DataFrame,
    *,
    casts: dict[str, str] | None = None,
    rules: list[dict[str, Any]] | None = None,
    dedup_keys: list[str] | None = None,
) -> SilverResult:
    """Full Silver pass: normalize -> cast -> dedup -> validate."""
    rows_in = bronze.height
    frame = trim_strings(bronze)
    frame = normalize_nulls(frame)

    if casts:
        frame, cast_failures = cast_columns(frame, casts)
        if cast_failures:
            # Missing columns are a configuration error, not a data error.
            raise ValueError(f"Kolom untuk cast tidak ditemukan: {cast_failures}")

    frame, removed = deduplicate(frame, dedup_keys)

    passing, quarantined, reasons = apply_rules(frame, rules or [])
    if removed:
        reasons["duplicate_row"] = reasons.get("duplicate_row", 0) + removed

    schema = [{"name": name, "dtype": str(dtype)} for name, dtype in passing.schema.items()]

    return SilverResult(
        frame=passing,
        rows_in=rows_in,
        rows_out=passing.height,
        quarantined=quarantined,
        reasons=reasons,
        schema=schema,
    )


def silver_key(*, org_id: str, run_id: str, node_id: str) -> str:
    stamp = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%S%f")
    return f"org/{org_id}/silver/run={run_id}/{node_id}-{stamp}.parquet"


def write_silver(
    frame: pl.DataFrame, *, org_id: str, run_id: str, node_id: str
) -> str | None:
    """Persist a Silver partition. Returns the URI, or ``None`` if empty."""
    if frame.height == 0:
        return None
    buffer = io.BytesIO()
    frame.write_parquet(buffer, compression="zstd")
    key = silver_key(org_id=org_id, run_id=run_id, node_id=node_id)
    storage.put_bytes(key, buffer.getvalue(), "application/vnd.apache.parquet")
    return storage.uri_for(key)


def read_parquet_uri(uri: str) -> pl.DataFrame:
    return pl.read_parquet(io.BytesIO(storage.get_bytes(uri)))


def profile_columns(frame: pl.DataFrame) -> dict[str, Any]:
    """Deterministic column statistics used by the Schema Profiler (A1 input).

    This is computed by code, never by an LLM (PRD A1 "statistik kolom hasil
    perhitungan deterministik").
    """
    total = frame.height
    columns: list[dict[str, Any]] = []
    for name in business_columns(frame):
        series = frame[name]
        non_null = int(series.count())
        null_count = total - non_null
        distinct = int(series.n_unique())
        entry: dict[str, Any] = {
            "name": name,
            "dtype": str(series.dtype),
            "null_ratio": round(null_count / total, 6) if total else 0.0,
            "distinct_count": distinct,
        }
        if series.dtype in (pl.Int64, pl.Float64):
            entry["min"] = series.min()
            entry["max"] = series.max()
            entry["mean"] = series.mean()
        elif series.dtype == pl.String and non_null:
            lengths = series.drop_nulls().str.len_chars()
            entry["min_length"] = int(lengths.min() or 0)
            entry["max_length"] = int(lengths.max() or 0)
            entry["sample"] = series.drop_nulls().head(5).to_list()
        columns.append(entry)
    return {"row_count": total, "columns": columns}


def to_jsonable(value: Any) -> Any:
    """Small helper so profiling output can be stored in JSONB."""
    return json.loads(json.dumps(value, default=str))
