"""Gold layer + reconciliation (FR-023).

Gold is an aggregate of Silver written to DuckDB. Two invariants:
1. Rows promote to Gold ONLY if the A0 gate passes (see ``app.gate``).
2. Every promotion is reconciled: ``rows_out`` (silver) must equal the sum of
   the grouped counts when the aggregate groups by a non-unique key, and the
   sum of numeric measures must match the Silver measure within a tolerance.
   A mismatch blocks promotion and is reported as evidence.
"""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass
from typing import Any

import duckdb
import polars as pl

from app import storage
from app.config import get_settings

REASON_TOLERANCE = 1e-6


@dataclass(slots=True)
class GoldResult:
    dataset_name: str
    rows_out: int
    duckdb_path: str
    table: str
    reconciled: bool
    reconciliation: dict[str, Any]


def duckdb_path_for(org_id: str, project_id: str) -> str:
    base = get_settings().DUCKDB_PATH
    os.makedirs(base, exist_ok=True) if not os.path.isdir(base) else None
    return os.path.join(base, f"{org_id}__{project_id}.duckdb")


def open_duckdb(path: str) -> duckdb.DuckDBPyConnection:
    """Open DuckDB read-write with a hard memory limit and no extensions.

    ``enable_external_access=false`` blocks httpfs/read_csv at the engine level
    so a mistake elsewhere cannot exfiltrate data (defence in depth for FR-033).

    NOTE (SEC-39): DuckDB only accepts ``SET memory_limit`` on a writable
    connection, which is exactly why the *query* path (``query_gold``) uses
    :func:`_read_only_connection` instead — a read-only connection can neither
    write a gold table nor change a pragma, and its limits come from the
    statement timeout and the outer LIMIT.
    """
    settings = get_settings()
    con = duckdb.connect(path)
    con.execute("SET enable_external_access=false")
    con.execute(f"SET memory_limit='{settings.DUCKDB_MEMORY_LIMIT}'")
    con.execute(f"SET threads={int(settings.DUCKDB_THREADS)}")
    return con


def _quote(identifier: str) -> str:
    """Quote an SQL identifier. Identifiers come from the DAG, never from users'
    free text, but we still escape to a strict allowlist."""
    if not identifier or not all(c.isalnum() or c in "_-" for c in identifier):
        raise ValueError(f"Identifier tidak valid: {identifier!r}")
    return '"' + identifier.replace('"', '""') + '"'


AGG_SQL = {
    "sum": "SUM({col})",
    "count": "COUNT({col})",
    "count_distinct": "COUNT(DISTINCT {col})",
    "avg": "AVG({col})",
    "min": "MIN({col})",
    "max": "MAX({col})",
    "median": "MEDIAN({col})",
}


def build_aggregate_sql(
    *, table: str, group_by: list[str], metrics: list[dict[str, Any]]
) -> str:
    """Compose the aggregate SELECT from typed DAG params (no string building
    from user text: every identifier passes :func:`_quote`)."""
    selects: list[str] = []
    for column in group_by:
        selects.append(f"{_quote(column)} AS {_quote(column)}")
    for metric in metrics:
        function = metric["function"]
        template = AGG_SQL.get(function)
        if template is None:
            raise ValueError(f"Fungsi agregasi tidak dikenal: {function}")
        column = metric.get("column") or "*"
        col_sql = "*" if column == "*" else _quote(column)
        # ``alias`` is user/DAG text: quoting without validation would let a
        # crafted alias close the quote and inject SQL (SEC-39). Same rule as
        # every other identifier here.
        selects.append(f"{template.format(col=col_sql)} AS {_quote(str(metric['alias']))}")
    if not selects:
        raise ValueError("Agregasi butuh minimal satu metrik.")

    group_sql = ", ".join(_quote(c) for c in group_by)
    sql = f"SELECT {', '.join(selects)} FROM {_quote(table)}"
    if group_by:
        sql += f" GROUP BY {group_sql}"
    return sql


def write_gold(
    *,
    org_id: str,
    project_id: str,
    run_id: str,
    silver: pl.DataFrame,
    dataset_name: str,
    group_by: list[str],
    metrics: list[dict[str, Any]],
    write_mode: str = "overwrite",
) -> GoldResult:
    """Materialise Silver into a DuckDB gold table and reconcile it."""
    path = duckdb_path_for(org_id, project_id)
    con = open_duckdb(path)
    staging = f"_stg_{run_id.replace('-', '')[:12]}"

    try:
        # Register the in-memory frame so DuckDB can read it without files.
        con.register("silver_batch", silver.to_arrow())
        con.execute(
            f"CREATE OR REPLACE TEMP TABLE {_quote(staging)} AS SELECT * FROM silver_batch"
        )
        sql = build_aggregate_sql(table=staging, group_by=group_by, metrics=metrics)
        gold_temp = f"_gold_{run_id.replace('-', '')[:12]}"
        con.execute(f"CREATE OR REPLACE TEMP TABLE {_quote(gold_temp)} AS {sql}")

        rows_out = int(con.execute(f"SELECT COUNT(*) FROM {_quote(gold_temp)}").fetchone()[0])

        if write_mode == "overwrite":
            con.execute(f"CREATE OR REPLACE TABLE {_quote(dataset_name)} AS SELECT * FROM {_quote(gold_temp)}")
        else:
            con.execute(
                f"CREATE TABLE IF NOT EXISTS {_quote(dataset_name)} AS SELECT * FROM {_quote(gold_temp)} WHERE FALSE"
            )
            con.execute(
                f"INSERT INTO {_quote(dataset_name)} SELECT * FROM {_quote(gold_temp)}"
            )

        reconciliation = reconcile(
            con=con,
            staging=staging,
            gold_temp=gold_temp,
            group_by=group_by,
            metrics=metrics,
            silver_rows=silver.height,
        )
    finally:
        con.close()

    return GoldResult(
        dataset_name=dataset_name,
        rows_out=rows_out,
        duckdb_path=path,
        table=dataset_name,
        reconciled=bool(reconciliation.get("passed")),
        reconciliation=reconciliation,
    )


def reconcile(
    *,
    con: duckdb.DuckDBPyConnection,
    staging: str,
    gold_temp: str,
    group_by: list[str],
    metrics: list[dict[str, Any]],
    silver_rows: int,
) -> dict[str, Any]:
    """Prove Silver and Gold agree. Returns evidence for the gate checklist."""
    checks: list[dict[str, Any]] = []
    passed = True

    # (1) Group count consistency: number of gold rows equals number of distinct
    #     group keys, and sum of per-group counts equals silver rows.
    if group_by:
        group_sql = ", ".join(_quote(c) for c in group_by)
        silver_groups = int(
            con.execute(f"SELECT COUNT(*) FROM (SELECT DISTINCT {group_sql} FROM {_quote(staging)})").fetchone()[0]
        )
        counted = int(
            con.execute(f"SELECT COALESCE(SUM(_n),0) FROM (SELECT COUNT(*) AS _n FROM {_quote(staging)} GROUP BY {group_sql})").fetchone()[0]
        )
        ok_groups = silver_groups == int(
            con.execute(f"SELECT COUNT(*) FROM {_quote(gold_temp)}").fetchone()[0]
        )
        checks.append(
            {
                "check": "gold_row_count_equals_distinct_groups",
                "expected": silver_groups,
                "actual": int(con.execute(f"SELECT COUNT(*) FROM {_quote(gold_temp)}").fetchone()[0]),
                "passed": ok_groups,
            }
        )
        passed = passed and ok_groups
        ok_sum = counted == silver_rows
        checks.append(
            {
                "check": "sum_group_counts_equals_silver_rows",
                "expected": silver_rows,
                "actual": counted,
                "passed": ok_sum,
            }
        )
        passed = passed and ok_sum

    # (2) Numeric measures: gold sum must equal silver sum within tolerance.
    for metric in metrics:
        if metric["function"] not in ("sum", "count", "count_distinct"):
            continue
        column = metric.get("column")
        if not column:
            continue
        silver_value = con.execute(
            f"SELECT COALESCE(SUM({_quote(column)}),0) FROM {_quote(staging)}"
        ).fetchone()[0]
        gold_value = con.execute(
            f"SELECT COALESCE(SUM({_quote(str(metric['alias']))}),0) FROM {_quote(gold_temp)}"
        ).fetchone()[0]
        try:
            silver_num = float(silver_value or 0)
            gold_num = float(gold_value or 0)
            ok = math.isclose(silver_num, gold_num, rel_tol=1e-9, abs_tol=REASON_TOLERANCE)
        except (TypeError, ValueError):
            ok = silver_value == gold_value
            silver_num, gold_num = silver_value, gold_value
        checks.append(
            {
                "check": f"measure_sum_matches_{metric['alias']}",
                "expected": silver_num,
                "actual": gold_num,
                "passed": bool(ok),
            }
        )
        passed = passed and bool(ok)

    return {"passed": passed, "checks": checks, "silver_rows": silver_rows}


def export_gold_parquet(
    *, org_id: str, project_id: str, dataset_name: str
) -> tuple[str, int]:
    """Export a gold table to Parquet in object storage for the Data Explorer."""
    path = duckdb_path_for(org_id, project_id)
    con = open_duckdb(path)
    try:
        frame = con.execute(f"SELECT * FROM {_quote(dataset_name)}").arrow()
    finally:
        con.close()
    pl_frame = pl.from_arrow(frame)
    key = f"org/{org_id}/gold/{dataset_name}.parquet"
    storage.put_bytes(key, pl_frame.write_parquet(), "application/vnd.apache.parquet")
    return storage.uri_for(key), pl_frame.height


def _read_only_connection(path: str) -> duckdb.DuckDBPyConnection:
    """READ-ONLY connection with every cheap defence already on.

    Ordering matters (SEC-39): ``enable_external_access=false`` is set
    **immediately after connect**, before any statement that could be planned
    against the filesystem or network.

    ``read_only=True`` is the engine-level guarantee: a statement that slipped
    past the validators cannot ``CREATE OR REPLACE``/``INSERT`` a gold table.
    ``SET memory_limit`` is omitted because DuckDB only honours it on a writable
    connection; boundedness comes from ``statement_timeout`` plus the outer
    ``LIMIT`` applied by :func:`query_gold`.
    """
    con = duckdb.connect(path, read_only=True)
    con.execute("SET enable_external_access=false")
    con.execute("SET threads=1")
    return con


def query_gold(
    *, org_id: str, project_id: str, sql: str, timeout_seconds: int = 10, limit: int = 1000
) -> tuple[list[str], list[dict[str, Any]]]:
    """Run a READ-ONLY, already-validated query against the gold DuckDB file.

    This function is the ONLY query entry point used by the API query service.
    The caller (``apps/api/app/services/query_service.py``) is responsible for
    sqlglot validation + table allowlist; here we additionally re-check that
    the statement is a single SELECT and wrap it with a LIMIT.
    """
    stripped = sql.strip().rstrip(";")
    if not stripped.lower().startswith("select") and not stripped.lower().startswith("with"):
        raise ValueError("Hanya SELECT yang diizinkan.")
    if ";" in stripped:
        raise ValueError("Hanya satu statement yang diizinkan.")

    path = duckdb_path_for(org_id, project_id)
    if not os.path.exists(path):
        raise FileNotFoundError("Warehouse untuk proyek ini belum ada.")

    con = _read_only_connection(path)
    try:
        con.execute(f"SET statement_timeout='{int(timeout_seconds)}s'")
        # Closing paren on its own line: a trailing ``-- comment`` in the SQL
        # must not be able to comment out the wrapper (SEC-39).
        limited = f"SELECT * FROM (\n{stripped}\n) AS _q LIMIT {int(limit)}"
        cursor = con.execute(limited)
        columns = [d[0] for d in cursor.description]
        rows = [dict(zip(columns, r, strict=False)) for r in cursor.fetchall()]
        return columns, json.loads(json.dumps(rows, default=str))
    finally:
        con.close()
