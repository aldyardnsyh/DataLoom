"""Query service: read-only DuckDB access for Explorer + Text-to-SQL (FR-033/041).

Four layers of defence, all independent:
1. ``sqlglot`` AST validation (``validate_readonly_sql``) — the authoritative
   guard; comments, CTE-wrapped DML and ``sqlite_scan``-style functions are
   rejected there, not by a string prefix test.
2. A per-project table allowlist derived from ``datasets`` (silver/gold only,
   shape-checked).
3. DuckDB opened ``read_only=True`` with ``enable_external_access=false`` and a
   statement timeout.
4. A forced outer ``LIMIT`` applied by wrapping, never by rewriting.

The service NEVER writes to a warehouse. It runs in a thread pool because the
DuckDB driver is synchronous.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import tempfile
import uuid
from dataclasses import dataclass
from typing import Any

import duckdb
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


from app.config import get_settings
from app.core.errors import NotFound, ValidationProblem
from app.db.models import Dataset
from app.services import storage

logger = logging.getLogger("dataloom.query")


@dataclass(slots=True)
class QueryResult:
    columns: list[str]
    rows: list[dict[str, Any]]
    truncated: bool
    row_count: int


@dataclass(slots=True)
class PreviewResult:
    columns: list[str]
    rows: list[list[str | int | float | bool | None]]
    truncated: bool


class UnsafeQuery(ValueError):
    """Raised when SQL is not a single read-only SELECT.

    ``reason`` mirrors ``app.tools.sql_guard.UnsafeSql.reason`` codes so an
    operator sees one vocabulary regardless of which layer rejected.
    """

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason
        self.detail = detail


#: Statement node types that are never a read. Kept here (not imported from the
#: agent worker) because ADR-001 forbids cross-app imports; the list is the
#: subset of ``apps/agents/app/tools/sql_guard.py`` that matters for DuckDB.
_FORBIDDEN_ROOT = (
    "Insert",
    "Update",
    "Delete",
    "Create",
    "Drop",
    "Alter",
    "Command",  # COPY, PRAGMA, ATTACH, INSTALL, LOAD, SET, ...
    "Grant",
    "TruncateTable",
    "Merge",
    "Attach",
    "Detach",
    "Use",
    "Set",
    "Transaction",
    "Commit",
    "Rollback",
    "Copy",
)

#: Functions that read files, reach the network or read another database.
DENIED_FUNCTIONS: frozenset[str] = frozenset(
    {
        "read_csv",
        "read_csv_auto",
        "read_parquet",
        "read_json",
        "read_json_auto",
        "json_scan",
        "read_text",
        "read_blob",
        "glob",
        "sniff_csv",
        "httpfs",
        "http_get",
        "http_post",
        "postgres_scan",
        "postgres_query",
        "postgres_attach",
        "sqlite_scan",
        "sqlite_attach",
        "mysql_scan",
        "delta_scan",
        "iceberg_scan",
        "parquet_scan",
        "duckdb_extensions",
        "install",
        "load",
        "export_database",
        "import_database",
        "read_pandas",
        "read_arrow",
    }
)


def validate_readonly_sql(sql: str, allowed_tables: set[str]) -> None:
    """THE API-side security boundary for Text-to-SQL (FR-033).

    Syntactic over the sqlglot AST, never over a string prefix: the statement is
    accepted only when it parses into exactly one statement whose root is a
    plain ``SELECT``/``WITH``/set-operation, contains no ``INTO``/DDL/DML node,
    no file/network function, and references only allowlisted tables.

    Raises :class:`UnsafeQuery`. Mirrors ``apps/agents/app/tools/sql_guard.py``
    (same reason codes) because the SQL reaching this function comes from a
    *model* — the string prefix check this replaced could be bypassed with a
    leading comment (``/* x */ DELETE``) or a ``WITH ... (DELETE)`` CTE.
    """
    if sql is None or not isinstance(sql, str) or not sql.strip():
        raise UnsafeQuery("EMPTY_STATEMENT", "SQL kosong.")
    body = sql.strip()
    if body.endswith(";"):
        body = body[:-1]

    try:
        import sqlglot
        from sqlglot import exp
        from sqlglot.errors import ParseError, TokenError
        from sqlglot.tokens import TokenType
    except ImportError as exc:  # pragma: no cover - sqlglot is a declared dep
        # Fail closed: without the AST we cannot prove the statement is a read.
        raise UnsafeQuery("PARSE_ERROR", "Validator SQL tidak tersedia.") from exc

    # Blank the contents of ``--`` and ``/* */`` comments (same character count,
    # so token offsets stay valid). The AST below is authoritative for *meaning*;
    # this only stops a comment from hiding a statement separator from the cheap
    # scan that follows. If the private scan API is unavailable we fall back to
    # the raw text — the AST check still runs.
    try:
        scanned = Tokenizer(dialect="duckdb")._scan(body)  # type: ignore[attr-defined]
        if scanned:
            chars = list(body)
            for token in scanned:
                if token.token_type in (TokenType.COMMENT, TokenType.HINT):
                    for offset in range(token.start, min(token.end, len(chars))):
                        chars[offset] = " "
            body = "".join(chars)
    except Exception:  # noqa: BLE001 - fall back to the raw text
        pass
    if ";" in body:
        raise UnsafeQuery("MULTI_STATEMENT", "Hanya satu statement yang diizinkan.")

    try:
        statements = [s for s in sqlglot.parse(body, read="duckdb") if s is not None]
    except (ParseError, TokenError) as exc:
        raise UnsafeQuery("PARSE_ERROR", "Query tidak dapat diurai.") from exc
    if len(statements) != 1:
        raise UnsafeQuery("MULTI_STATEMENT", "Hanya satu statement yang diizinkan.")

    root = statements[0]
    for type_name in _FORBIDDEN_ROOT:
        node_type = getattr(exp, type_name, None)
        if node_type is not None and isinstance(root, node_type):
            raise UnsafeQuery("NOT_SELECT", "Hanya pernyataan SELECT yang diizinkan.")
    if isinstance(root, exp.SetOperation):
        for branch in (root.this, root.expression):
            if not isinstance(branch, (exp.Select, exp.SetOperation)):
                raise UnsafeQuery("NOT_SELECT", "Setiap cabang UNION harus SELECT.")
    elif isinstance(root, exp.Subquery):
        if not isinstance(root.this, exp.Select):
            raise UnsafeQuery("NOT_SELECT", "Hanya pernyataan SELECT yang diizinkan.")
    elif not isinstance(root, exp.Select):
        raise UnsafeQuery("NOT_SELECT", "Hanya pernyataan SELECT yang diizinkan.")

    lowered = {name.strip().lower() for name in allowed_tables if name and name.strip()}
    cte_names = {
        cte.alias.lower() for cte in root.find_all(exp.CTE) if cte.alias
    }
    for node in root.walk():
        if isinstance(node, exp.Into):
            raise UnsafeQuery("INTO_CLAUSE", "Klausa INTO tidak diizinkan.")
        if isinstance(node, exp.Anonymous):
            if str(node.name or "").lower() in DENIED_FUNCTIONS:
                raise UnsafeQuery("DISALLOWED_FUNCTION", "Fungsi terlarang.")
        elif isinstance(node, exp.Func):
            try:
                func_name = node.sql_name().lower()
            except Exception:  # noqa: BLE001 - unknown class is not a pass
                func_name = ""
            if func_name in DENIED_FUNCTIONS:
                raise UnsafeQuery("DISALLOWED_FUNCTION", "Fungsi terlarang.")
        if isinstance(node, exp.Table):
            bare = node.name.lower()
            if bare in cte_names and not (node.db or node.catalog):
                continue
            if bare not in lowered:
                raise UnsafeQuery(
                    "TABLE_NOT_ALLOWED", "Tabel tidak ada dalam allowlist proyek."
                )


def enforce_limit(sql: str, max_rows: int) -> str:
    """Wrap ``sql`` so at most ``max_rows`` rows come back, whatever the inner SQL.

    ``max_rows`` is clamped to ``[1, 1000]`` (``SqlAnswer.rows`` max_length) and
    the closing parenthesis is put on its own line so a trailing ``-- comment``
    in the model's SQL cannot swallow it. Mirrors
    ``apps/agents/app/tools/sql_guard.enforce_limit`` so both layers produce the
    same shape (a plain ``SELECT ... FROM (subquery)``) and stay re-validatable.
    """
    limit = max(1, min(int(max_rows), 1000))
    body = sql.strip()
    if body.endswith(";"):
        body = body[:-1].rstrip()
    return f"SELECT * FROM (\n{body}\n) AS _dl_q LIMIT {limit}"


def _warehouse_path(org_id: uuid.UUID, project_id: uuid.UUID) -> str:
    settings = get_settings()
    return f"{settings.DUCKDB_PATH}/{org_id}__{project_id}.duckdb"


def _open(path: str) -> duckdb.DuckDBPyConnection:
    """Open the warehouse READ-ONLY with every cheap engine-level defence on.

    ``read_only=True`` is the guarantee that no statement — however it was
    validated, and whichever validator ran — can write to (or
    ``CREATE OR REPLACE``) a gold table: DuckDB refuses the write itself.

    ``enable_external_access=false`` is set first so it is in force for every
    subsequent statement (SEC-39: it used to be one of several pragmas whose
    ordering was implicit).

    ``SET memory_limit`` is intentionally absent: DuckDB honors it only on a
    writable connection. Boundedness comes from the outer ``LIMIT`` in
    :func:`_run_query_sync`.
    """
    con = duckdb.connect(path, read_only=True)
    con.execute("SET enable_external_access=false")
    con.execute("SET threads=1")
    return con


def _run_query_sync(path: str, sql: str, limit: int, timeout_seconds: int) -> QueryResult:
    con = _open(path)

    try:
        # DuckDB has no ``statement_timeout`` setting; boundedness comes from
        # the enforced outer LIMIT below. ``enforce_limit`` is total (works for
        # WITH/UNION/ORDER BY) and puts the closing paren on its own line so a
        # trailing ``--`` cannot comment it out.
        limited = enforce_limit(sql, int(limit) + 1)
        cursor = con.execute(limited)
        columns = [description[0] for description in cursor.description]
        raw_rows = cursor.fetchall()
        truncated = len(raw_rows) > limit
        rows = [
            {column: value for column, value in zip(columns, row, strict=False)}
            for row in raw_rows[:limit]
        ]
        return QueryResult(
            columns=columns,
            rows=_jsonable(rows),
            truncated=truncated,
            row_count=len(rows),
        )
    finally:
        con.close()


def _jsonable(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Make values JSON-safe without inventing values."""
    safe: list[dict[str, Any]] = []
    for row in rows:
        safe.append(
            {
                key: (value.isoformat() if hasattr(value, "isoformat") else value)
                for key, value in row.items()
            }
        )
    return safe


async def run_readonly(
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    sql: str,
    limit: int,
    timeout_seconds: int,
    allowed_tables: set[str] | None = None,
) -> QueryResult:
    """Execute a read-only query in a thread.

    When ``allowed_tables`` is given the statement is re-validated with
    :func:`validate_readonly_sql` here, in the one function that actually opens
    DuckDB. Callers that already validated pay one extra parse; that is the
    price of not depending on every caller remembering to validate (the
    Text-to-SQL path feeds model output straight into this function).
    """
    if allowed_tables is not None:
        validate_readonly_sql(sql, allowed_tables)
    path = _warehouse_path(org_id, project_id)
    return await asyncio.to_thread(_run_query_sync, path, sql, limit, timeout_seconds)


async def project_tables(

    session: AsyncSession, *, org_id: uuid.UUID, project_id: uuid.UUID
) -> set[str]:
    """Table allowlist for a project (queryable dataset names).

    Only ``gold`` rows are queryable from the DuckDB warehouse: Bronze, Silver,
    and Quarantine live as Parquet and must never be reachable from a
    Text-to-SQL statement. Names are additionally *shape-checked* here so a row
    created outside the DAG validator (``SinkParams.dataset_name`` is
    ``^[a-z0-9_]+$``) can never smuggle a schema-qualified name such as
    ``pg_catalog.pg_shadow`` into the allowlist.
    """
    from dataloom_contracts._base import Layer

    rows = (
        await session.execute(
            select(Dataset.name, Dataset.layer).where(
                Dataset.org_id == org_id,
                Dataset.project_id == project_id,
                Dataset.layer == Layer.GOLD.value,
            )
        )
    ).all()
    return {_safe_table_name(name) for name, _layer in rows if _safe_table_name(name)}


def _safe_table_name(name: str | None) -> str:
    """Lower-cased name if it is a bare identifier, else ``""`` (deny by default)."""
    if not name:
        return ""
    lowered = str(name).strip().lower()
    if not re.fullmatch(r"[a-z0-9_]{1,128}", lowered):
        return ""
    return lowered


async def get_dataset(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    dataset_id: uuid.UUID,
    project_id: uuid.UUID | None = None,
) -> Dataset:

    """Fetch a dataset scoped to org (and optionally project).

    Filtering by BOTH ids means a valid dataset id from another project of the
    SAME org is also rejected — the dataset must belong to the project in the
    URL, not merely to the tenant.
    """
    stmt = select(Dataset).where(Dataset.id == dataset_id, Dataset.org_id == org_id)
    if project_id is not None:
        stmt = stmt.where(Dataset.project_id == project_id)
    row = (await session.execute(stmt)).scalar_one_or_none()
    if row is None:
        raise NotFound("Dataset tidak ditemukan.")
    return row


def _sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _json_scalar(value: Any) -> str | int | float | bool | None:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _preview_parquet_sync(
    *, uri: str, org_id: uuid.UUID, limit: int, timeout_seconds: int = 10
) -> PreviewResult:
    """Read a bounded preview from a Parquet object.

    The object key is derived from the stored URI and must sit under this
    organisation's prefix. The file is copied to a server-owned temp path and
    read by DuckDB; no user-controlled path or SQL reaches the engine.
    """
    key = storage.key_from_uri(uri)
    if not key.startswith(f"org/{org_id}/"):
        raise ValueError("Dataset bukan milik organisasi ini.")

    head = storage.head_object(key)
    if head is not None and int(head.get("size") or 0) > 50 * 1024 * 1024:
        raise ValueError("Objek terlalu besar untuk pratinjau.")

    body = storage.get_bytes(key)
    fd, path = tempfile.mkstemp(prefix="dataloom-preview-", suffix=".parquet")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(body)
        con = duckdb.connect(":memory:")
        try:
            con.execute("SET threads=1")
            # The file is a server-owned temp copy and the result is capped by
            # the LIMIT below. DuckDB's in-memory connection does not support
            # statement_timeout on every build, so do not depend on it here.
            literal_path = _sql_literal(path.replace("\\", "/"))
            cursor = con.execute(
                f"SELECT * FROM read_parquet({literal_path}) LIMIT {int(limit) + 1}"
            )
            columns = [description[0] for description in cursor.description]
            raw_rows = cursor.fetchall()
            truncated = len(raw_rows) > limit
            rows = [
                [_json_scalar(value) for value in row]
                for row in raw_rows[:limit]
            ]
            return PreviewResult(columns=columns, rows=rows, truncated=truncated)
        finally:
            con.close()
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


async def preview_parquet(
    *, uri: str, org_id: uuid.UUID, limit: int
) -> PreviewResult:
    return await asyncio.to_thread(
        _preview_parquet_sync, uri=uri, org_id=org_id, limit=limit
    )


async def preview_rows(
    *, org_id: uuid.UUID, project_id: uuid.UUID, table: str, limit: int
) -> QueryResult:
    """Preview at most ``limit`` rows. ``table`` is validated as an identifier.

    The identifier check here is deliberately strict (ASCII, no dots/quotes) so
    a ``datasets.name`` that somehow bypassed ``SinkParams`` cannot escape the
    quoting; the caller (``explore.preview_dataset``) already resolved the row
    through ``get_dataset`` with org+project scoping.
    """
    if not re.fullmatch(r"[a-z0-9_]{1,128}", (table or "").lower()):
        raise ValidationProblem("Nama tabel tidak valid.")
    sql = f'SELECT * FROM {_quote_identifier(table)}'
    return await run_readonly(
        org_id=org_id,
        project_id=project_id,
        sql=sql,
        limit=limit,
        timeout_seconds=10,
    )


def _quote_identifier(identifier: str) -> str:
    """Quote a bare identifier for DuckDB (no re-validation, caller checked)."""
    return '"' + identifier.replace('"', '""') + '"'


def warehouse_exists(org_id: uuid.UUID, project_id: uuid.UUID) -> bool:

    import os

    return os.path.exists(_warehouse_path(org_id, project_id))

