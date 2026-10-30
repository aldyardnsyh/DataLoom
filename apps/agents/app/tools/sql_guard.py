"""sqlglot-based read-only SQL validator (FR-033). THE security boundary.

Every SQL statement produced by a model passes through
:func:`validate_readonly_sql` before it reaches DuckDB. The validator is
*syntactic and semantic over the AST*, never over a string prefix: a statement
is accepted only if sqlglot parses it into exactly one ``SELECT`` (optionally
inside ``WITH``) whose table references and functions are all allowlisted.

Rejection rules (each has its own ``reason`` code)
-------------------------------------------------
1. empty / whitespace only                     -> ``EMPTY_STATEMENT``
2. more than one statement (or a trailing ``;``) -> ``MULTI_STATEMENT``
3. not a ``SELECT`` (DDL/DML/COPY/PRAGMA/...)   -> ``NOT_SELECT``
4. any ``INTO`` clause                          -> ``INTO_CLAUSE``
5. a table outside the allowlist                -> ``TABLE_NOT_ALLOWED``
6. a denylisted function                        -> ``DISALLOWED_FUNCTION``
7. subquery / CTE depth above :data:`MAX_QUERY_DEPTH` -> ``QUERY_TOO_COMPLEX``
8. a ``UNION`` branch that is not a plain SELECT -> ``NOT_SELECT``
9. parser failure (sqlglot raises)              -> ``PARSE_ERROR``

The caller still wraps the statement with
:func:`enforce_limit` and executes it on a connection where
``enable_external_access=false`` is set. Defence in depth, not a substitute.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import Any

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError, TokenError

logger = logging.getLogger("dataloom.agents.sql_guard")

#: DuckDB dialect is the only one accepted (the query target is DuckDB).
DEFAULT_DIALECT = "duckdb"

#: Statement node types explicitly refused, mapped to a stable reason.
FORBIDDEN_STATEMENT_TYPES: tuple[type[exp.Expression], ...] = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Create,
    exp.Drop,
    exp.Alter,
    exp.Command,  # COPY, PRAGMA, ATTACH, INSTALL, LOAD, SET, EXPORT, IMPORT...
    exp.Grant,
    exp.TruncateTable,
    exp.Merge,
)

#: Function names (lower-case) that can read files, reach the network or read
#: other databases. DuckDB's ``enable_external_access=false`` also blocks these
#: at the engine level; this list is the static half of that control.
DENIED_FUNCTIONS: frozenset[str] = frozenset(
    {
        "read_csv",
        "read_csv_auto",
        "read_parquet",
        "parquet_scan",
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
        "url_encode",
        "postgres_scan",
        "postgres_query",
        "postgres_attach",
        "sqlite_scan",
        "sqlite_attach",
        "mysql_scan",
        "delta_scan",
        "iceberg_scan",
        "duckdb_extensions",
        "install",
        "load",
        "export_database",
        "import_database",
        "pg_read_file",
        "pg_read_binary_file",
        "read_pandas",
        "read_arrow",
    }
)

#: Nodes that mean "this is not a plain read query".
FORBIDDEN_NODES: tuple[type[exp.Expression], ...] = (
    exp.Into,
    exp.Attach,
    exp.Detach,
    exp.Use,
    exp.Set,
    exp.Transaction,
    exp.Commit,
    exp.Rollback,
    exp.Copy,
)

#: Maximum nesting depth of CTEs + subqueries. Bounded so a pathological
#: query cannot exhaust the parser or the planner.
MAX_QUERY_DEPTH = 8


class UnsafeSql(ValueError):
    """Raised when a statement must not reach the database.

    ``reason`` is the stable code stored in ``SqlAnswer.rejection_code``
    (``NOT_SELECT``, ``TABLE_NOT_ALLOWED``, ``MULTI_STATEMENT``,
    ``DISALLOWED_FUNCTION``, ``INTO_CLAUSE``, ``PARSE_ERROR``,
    ``QUERY_TOO_COMPLEX``, ``EMPTY_STATEMENT``). ``detail`` is a short
    Indonesian sentence for humans and never contains the full SQL text.
    """

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason
        self.detail = detail


# ---------------------------------------------------------------------------
# Low-level checks
# ---------------------------------------------------------------------------
def _parse_one(sql: str, dialect: str) -> exp.Expression:
    try:
        statements = sqlglot.parse(sql, read=dialect)
    except (ParseError, TokenError) as exc:
        # ``str(exc)`` echoes the SQL; keep only the class name in the message.
        raise UnsafeSql("PARSE_ERROR", f"SQL tidak dapat diurai ({type(exc).__name__}).") from exc
    parsed = [s for s in statements if s is not None]
    if len(parsed) == 0:
        raise UnsafeSql("EMPTY_STATEMENT", "SQL kosong.")
    if len(parsed) > 1:
        raise UnsafeSql("MULTI_STATEMENT", "Hanya satu statement yang diizinkan.")
    return parsed[0]


def _assert_select_only(root: exp.Expression) -> exp.Select:
    """The root must be a SELECT, or a WITH that resolves to a SELECT."""
    if isinstance(root, exp.SetOperation):
        # ``UNION`` / ``INTERSECT`` / ``EXCEPT``: every branch must be a SELECT.
        return _require_select_from_setop(root)

    for node_type in FORBIDDEN_STATEMENT_TYPES:
        if isinstance(root, node_type):
            raise UnsafeSql("NOT_SELECT", "Hanya pernyataan SELECT yang diizinkan.")

    if isinstance(root, exp.Select):
        # ``WITH ... SELECT`` parses as exp.Select with a ``with`` arg, and the
        # CTEs themselves are Select nodes, so one check covers both.
        return root

    if isinstance(root, exp.Subquery) and isinstance(root.this, exp.Select):
        # ``(SELECT ...)`` alone: legal, treated as a subquery select.
        return root.this

    raise UnsafeSql("NOT_SELECT", "Hanya pernyataan SELECT yang diizinkan.")


def _require_select_from_setop(node: exp.Expression) -> exp.Select:
    """Walk every branch of a set operation and require plain SELECTs."""
    if isinstance(node, exp.Select):
        return node
    if isinstance(node, exp.SetOperation):
        # Both branches are checked; the return value is only used for typing.
        _require_select_from_setop(node.this)
        return _require_select_from_setop(node.expression)
    raise UnsafeSql("NOT_SELECT", "Setiap cabang UNION harus berupa SELECT.")


def _walk(node: exp.Expression) -> Iterable[exp.Expression]:
    """Depth-first walk over sqlglot's ``args`` tree (explicit, no helpers)."""
    stack: list[exp.Expression] = [node]
    while stack:
        current = stack.pop()
        yield current
        for value in current.args.values():
            if isinstance(value, exp.Expression):
                stack.append(value)
            elif isinstance(value, list):
                stack.extend(item for item in value if isinstance(item, exp.Expression))


def _assert_no_into(root: exp.Expression) -> None:
    for node in _walk(root):
        if isinstance(node, exp.Into):
            raise UnsafeSql("INTO_CLAUSE", "Klausa INTO tidak diizinkan.")
        for node_type in FORBIDDEN_NODES:
            if isinstance(node, node_type):
                raise UnsafeSql("NOT_SELECT", "Pernyataan ini bukan query baca-saja.")


def _assert_functions_allowed(root: exp.Expression) -> None:
    for node in _walk(root):
        if isinstance(node, exp.Anonymous):
            name = str(node.name or "").lower()
            if name in DENIED_FUNCTIONS:
                raise UnsafeSql(
                    "DISALLOWED_FUNCTION", f"Fungsi '{name}' tidak diizinkan."
                )
        elif isinstance(node, exp.Func):
            # ``sql_name`` covers built-ins (e.g. exp.ReadCSV -> read_csv).
            try:
                name = node.sql_name().lower()
            except Exception:  # noqa: BLE001 - unknown func class
                name = ""
            if name in DENIED_FUNCTIONS:
                raise UnsafeSql(
                    "DISALLOWED_FUNCTION", f"Fungsi '{name}' tidak diizinkan."
                )


def _normalise_table_name(table: exp.Table) -> str:
    """Fully-qualified, lower-cased name used for the allowlist comparison."""
    parts: list[str] = []
    if table.catalog:
        parts.append(str(table.catalog))
    if table.db:
        parts.append(str(table.db))
    parts.append(str(table.name))
    return ".".join(parts).lower()


def _assert_tables_allowed(root: exp.Expression, allowed: set[str]) -> None:
    """Every physical table must be allowlisted.

    ``allowed`` may contain bare names (``silver_orders``) and qualified names
    (``main.silver_orders``). A qualified reference matches when either the
    full name or its final component is present.

    CTE aliases shadow real tables: a reference whose bare name is a CTE alias
    (and which is not schema-qualified) is skipped.
    """
    lowered = {name.strip().lower() for name in allowed if name and name.strip()}
    ctes = _cte_names(root)
    for table in root.find_all(exp.Table):
        bare = table.name.lower()
        if bare in ctes and not (table.db or table.catalog):
            continue
        qualified = _normalise_table_name(table)
        if qualified in lowered or bare in lowered:
            continue
        raise UnsafeSql(
            "TABLE_NOT_ALLOWED",
            f"Tabel '{bare}' tidak ada dalam daftar tabel yang diizinkan.",
        )


def _cte_names(root: exp.Expression) -> set[str]:
    names: set[str] = set()
    for cte in root.find_all(exp.CTE):
        alias = cte.alias
        if alias:
            names.add(alias.lower())
    return names


def _assert_depth(root: exp.Expression) -> None:
    """Bound nesting so a pathological query cannot exhaust parser/planner."""
    for node in root.find_all(exp.Select):
        level = 0
        parent = node.parent
        while parent is not None:
            if isinstance(parent, exp.Subquery | exp.Select | exp.SetOperation):
                level += 1
            parent = parent.parent
        if level > MAX_QUERY_DEPTH:
            raise UnsafeSql("QUERY_TOO_COMPLEX", "Query terlalu bertingkat.")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def parse_and_validate(sql: str, allowed_tables: set[str], dialect: str = DEFAULT_DIALECT) -> exp.Expression:
    """Validate and return the parsed root node (callers may inspect it).

    Raises :class:`UnsafeSql` on any violation. Never executes anything.
    """
    if sql is None or not isinstance(sql, str) or not sql.strip():
        raise UnsafeSql("EMPTY_STATEMENT", "SQL kosong.")

    stripped = sql.strip()
    # A trailing semicolon is tolerated on the LAST statement only; any other
    # semicolon means the caller is trying to chain statements.
    body = stripped[:-1] if stripped.endswith(";") else stripped
    if ";" in body:
        raise UnsafeSql("MULTI_STATEMENT", "Hanya satu statement yang diizinkan.")

    root = _parse_one(body, dialect)

    _assert_select_only(root)
    _assert_no_into(root)
    _assert_functions_allowed(root)
    _assert_depth(root)
    _assert_tables_allowed(root, allowed_tables)
    return root


def validate_readonly_sql(sql: str, allowed_tables: set[str], dialect: str = DEFAULT_DIALECT) -> None:
    """Raise :class:`UnsafeSql` unless ``sql`` is a single safe SELECT.

    This is the function every caller uses (graph, tests, API). It returns
    ``None`` on success so a mistake like ``if validate_readonly_sql(...)`` is
    a type error rather than a silent pass.
    """
    parse_and_validate(sql, allowed_tables, dialect)


def enforce_limit(sql: str, max_rows: int, dialect: str = DEFAULT_DIALECT) -> str:
    """Wrap ``sql`` so at most ``max_rows`` rows can be returned.

    Wrapping (rather than rewriting) is used because it is total: it works for
    ``WITH``, ``UNION``, ``ORDER BY`` and ``LIMIT``-bearing statements alike.
    ``max_rows`` is clamped to ``[1, 1000]`` to match the contract cap on
    ``SqlAnswer.rows``.

    The returned string is a NEW statement and must itself be validated before
    execution; :func:`validate_readonly_sql` accepts it because the wrapper is
    a plain ``SELECT ... FROM (subquery)``.
    """
    limit = max(1, min(int(max_rows), 1000))
    body = sql.strip()
    if body.endswith(";"):
        body = body[:-1].rstrip()
    # A trailing ``-- comment`` would swallow the closing parenthesis, so the
    # wrapper is built on its own line.
    return f"SELECT * FROM (\n{body}\n) AS _dl_q LIMIT {limit}"


def write_statement_kind(sql: str, dialect: str = DEFAULT_DIALECT) -> str:
    """Best-effort label of the root statement, for safe logging/telemetry.

    Returns ``"select"``, ``"with"``, ``"dml"``, ``"ddl"`` or ``"unknown"``.
    Never raises: this is diagnostics, not enforcement.
    """
    try:
        statements = sqlglot.parse(sql, read=dialect)
    except Exception:  # noqa: BLE001
        return "unknown"
    if not statements:
        return "unknown"
    root: Any = statements[0]
    if isinstance(root, exp.With):
        return "with"
    if isinstance(root, exp.Select):
        return "with" if root.args.get("with") or root.args.get("with_") else "select"
    if isinstance(root, (exp.Insert, exp.Update, exp.Delete, exp.Merge)):
        return "dml"
    if isinstance(root, (exp.Create, exp.Drop, exp.Alter)):
        return "ddl"
    return "unknown"


__all__ = [
    "DENIED_FUNCTIONS",
    "DEFAULT_DIALECT",
    "MAX_QUERY_DEPTH",
    "UnsafeSql",
    "enforce_limit",
    "parse_and_validate",
    "validate_readonly_sql",
    "write_statement_kind",
]
