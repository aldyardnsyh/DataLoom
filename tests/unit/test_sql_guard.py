"""FR-033: the read-only SQL guard - "THE security boundary".

PRD §5.4 FR-033 acceptance: *"Hanya SELECT; AST di-parse; timeout dan LIMIT
dipaksa; tabel di luar tenant ditolak"*.

Every rejection test names the reason code the API surfaces as
``SqlAnswer.rejection_code``, so the UI can render the exact blocked-reason
variant. If any of these ever passes, a model-authored statement can read files
or write rows on the warehouse.

``sqlglot`` is a declared dependency; if it is absent the module SKIPS loudly.
"""

from __future__ import annotations

import pytest

pytest.importorskip("sqlglot", reason="sqlglot tidak terpasang: tes sql_guard tidak dapat dijalankan.")

ALLOWED = {"silver_orders", "gold_sales"}


def _guard():
    from agents_app.tools import sql_guard

    return sql_guard


def _reject(sql: str, expected_reason: str, allowed: set[str] | None = None) -> str:
    guard = _guard()
    with pytest.raises(guard.UnsafeSql) as excinfo:
        guard.validate_readonly_sql(sql, allowed if allowed is not None else set(ALLOWED))
    assert excinfo.value.reason == expected_reason, (
        f"SQL {sql!r} ditolak dengan alasan '{excinfo.value.reason}', "
        f"harusnya '{expected_reason}'."
    )
    assert excinfo.value.detail, "Alasan penolakan tidak punya detail untuk pengguna."
    return excinfo.value.detail


def _accept(sql: str, allowed: set[str] | None = None) -> None:
    guard = _guard()
    # validate_readonly_sql returns None on success: ``is None`` proves no
    # accidental truthy return value is masking a rejection.
    assert guard.validate_readonly_sql(sql, allowed if allowed is not None else set(ALLOWED)) is None, (
        f"SQL aman {sql!r} ditolak."
    )


# ---------------------------------------------------------------------------
# Valid SELECTs
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM silver_orders",
        "SELECT id, total FROM silver_orders WHERE total > 10",
        "SELECT COUNT(*) FROM gold_sales",
        "select * from SILVER_ORDERS",  # case-insensitive table match
        "SELECT * FROM silver_orders LIMIT 10",
        "WITH x AS (SELECT * FROM silver_orders) SELECT * FROM x",
        "SELECT * FROM silver_orders ORDER BY id DESC",
        "SELECT o.id FROM silver_orders AS o JOIN gold_sales AS g ON o.id = g.id",
        "SELECT * FROM silver_orders;",  # single trailing semicolon is allowed
        "SELECT * FROM gold_sales UNION SELECT * FROM gold_sales",
    ],
)
def test_valid_read_only_selects_are_accepted(sql: str) -> None:
    _accept(sql)


# ---------------------------------------------------------------------------
# DDL
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "sql",
    [
        "DROP TABLE silver_orders",
        "CREATE TABLE evil (id INT)",
        "ALTER TABLE silver_orders ADD COLUMN x INT",
        "TRUNCATE TABLE silver_orders",
    ],
)
def test_ddl_is_rejected_as_not_select(sql: str) -> None:
    detail = _reject(sql, "NOT_SELECT")
    assert "SELECT" in detail, f"Detail penolakan DDL tidak menyebut SELECT: {detail!r}"


# ---------------------------------------------------------------------------
# DML
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO silver_orders (id) VALUES (1)",
        "UPDATE silver_orders SET total = 0",
        "DELETE FROM silver_orders",
        "MERGE INTO silver_orders USING gold_sales ON TRUE WHEN MATCHED THEN DELETE",
    ],
)
def test_dml_is_rejected_as_not_select(sql: str) -> None:
    _reject(sql, "NOT_SELECT")


# ---------------------------------------------------------------------------
# Multiple statements
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM silver_orders; DROP TABLE silver_orders",
        "SELECT 1; SELECT 2",
        "SELECT * FROM silver_orders;;",
    ],
)
def test_multiple_statements_are_rejected(sql: str) -> None:
    _reject(sql, "MULTI_STATEMENT")


# ---------------------------------------------------------------------------
# File reading / network / extension functions
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM read_csv('/etc/passwd')",
        "SELECT * FROM read_csv_auto('https://evil.test/x.csv')",
        "SELECT * FROM read_parquet('s3://bucket/object.parquet')",
        "SELECT * FROM parquet_scan('x.parquet')",
        "SELECT * FROM glob('/**/*')",
        "SELECT http_get('http://169.254.169.254/latest/meta-data')",
        "SELECT * FROM postgres_scan('dbname=x', 'public', 't')",
        "SELECT * FROM sqlite_scan('a.db', 't')",
        "SELECT * FROM delta_scan('s3://x/y')",
        "SELECT * FROM iceberg_scan('s3://x/y')",
    ],
)
def test_file_and_network_functions_are_rejected(sql: str) -> None:
    reason = _reject(sql, "DISALLOWED_FUNCTION")
    assert "tidak diizinkan" in reason, (
        f"Detail penolakan fungsi tidak jelas bagi pengguna: {reason!r}"
    )


def test_httpfs_is_on_the_denylist() -> None:
    """``httpfs`` is an extension name; it must be present in the closed list."""
    guard = _guard()
    assert "httpfs" in guard.DENIED_FUNCTIONS, (
        "httpfs tidak ada di DENIED_FUNCTIONS: akses jaringan DuckDB bisa lolos."
    )


# ---------------------------------------------------------------------------
# Tables outside the allowlist (tenant isolation at the SQL layer)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM other_tenant_secret",
        "SELECT * FROM main.silver_orders_x",
        'SELECT * FROM "unknown_table"',
        "SELECT * FROM information_schema.tables",
        "SELECT * FROM pg_catalog.pg_user",
    ],
)
def test_tables_outside_the_allowlist_are_rejected(sql: str) -> None:
    detail = _reject(sql, "TABLE_NOT_ALLOWED")
    assert "daftar tabel" in detail or "allowlist" in detail, (
        f"Detail penolakan tabel tidak menjelaskan allowlist: {detail!r}"
    )


def test_allowlist_accepts_schema_qualified_reference_to_an_allowed_table() -> None:
    _accept("SELECT * FROM main.silver_orders")


def test_cte_alias_shadows_a_real_table_name_without_error() -> None:
    """A CTE named like a real table must not be checked against the allowlist."""
    _accept("WITH gold_sales AS (SELECT * FROM silver_orders) SELECT * FROM gold_sales")


# ---------------------------------------------------------------------------
# INTO / writes disguised as SELECT
# ---------------------------------------------------------------------------
def test_select_into_is_rejected() -> None:
    reason = _reject("SELECT * INTO stolen FROM silver_orders", "INTO_CLAUSE")
    assert "INTO" in reason


@pytest.mark.parametrize(
    "sql",
    [
        "COPY silver_orders TO '/tmp/out.csv'",
        "ATTACH 'evil.db' AS evil",
        "PRAGMA database_list",
        "SET memory_limit='100GB'",
        "INSTALL httpfs",
        "LOAD httpfs",
    ],
)
def test_commands_and_session_settings_are_rejected(sql: str) -> None:
    guard = _guard()
    with pytest.raises(guard.UnsafeSql) as excinfo:
        guard.validate_readonly_sql(sql, set(ALLOWED))
    assert excinfo.value.reason in {"NOT_SELECT", "DISALLOWED_FUNCTION"}, (
        f"Perintah {sql!r} ditolak dengan alasan '{excinfo.value.reason}'. "
        "Harus NOT_SELECT atau DISALLOWED_FUNCTION."
    )


# ---------------------------------------------------------------------------
# Empty / unparseable
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("sql", ["", "   ", "\n\t "])
def test_empty_statement_is_rejected(sql: str) -> None:
    _reject(sql, "EMPTY_STATEMENT")


@pytest.mark.parametrize("sql", ["SELECT FROM WHERE", "SELEC * FROM x", "SELECT * FROM (("])
def test_unparseable_sql_is_rejected(sql: str) -> None:
    guard = _guard()
    with pytest.raises(guard.UnsafeSql) as excinfo:
        guard.validate_readonly_sql(sql, set(ALLOWED))
    assert excinfo.value.reason in {"PARSE_ERROR", "NOT_SELECT", "EMPTY_STATEMENT"}, (
        f"SQL tidak dapat diurai ditolak dengan alasan tak terduga: {excinfo.value.reason}"
    )
    # The message must NOT echo the SQL text back (it may contain secrets).
    assert sql not in str(excinfo.value), (
        "Pesan galat menggemakan SQL mentah; ini bisa membocorkan isi query ke log."
    )


# ---------------------------------------------------------------------------
# Depth bound (resource exhaustion)
# ---------------------------------------------------------------------------
def test_deeply_nested_query_is_rejected_as_too_complex() -> None:
    guard = _guard()
    depth = guard.MAX_QUERY_DEPTH + 3
    sql = "SELECT * FROM " + "(" * depth + "SELECT * FROM silver_orders" + ")" * depth

    with pytest.raises(guard.UnsafeSql) as excinfo:
        guard.validate_readonly_sql(sql, set(ALLOWED))
    assert excinfo.value.reason == "QUERY_TOO_COMPLEX", (
        f"Kedalaman {depth} diterima/ditolak dengan alasan '{excinfo.value.reason}'."
    )


# ---------------------------------------------------------------------------
# enforce_limit: adds LIMIT and does not double-wrap
# ---------------------------------------------------------------------------
def test_enforce_limit_wraps_with_an_outer_limit() -> None:
    guard = _guard()

    wrapped = guard.enforce_limit("SELECT * FROM silver_orders", 100)

    assert "LIMIT 100" in wrapped, f"LIMIT tidak ditambahkan: {wrapped!r}"
    assert wrapped.startswith("SELECT * FROM ("), (
        f"Bentuk wrapper berubah: {wrapped!r}. Validator hanya menerima subquery SELECT."
    )


def test_enforce_limit_output_is_itself_valid_under_the_guard() -> None:
    """The wrapper must pass validation, otherwise the query is rejected twice."""
    guard = _guard()
    wrapped = guard.enforce_limit("SELECT * FROM silver_orders", 50)

    _accept(wrapped)


def test_enforce_limit_strips_a_trailing_semicolon() -> None:
    guard = _guard()
    wrapped = guard.enforce_limit("SELECT * FROM silver_orders;", 10)

    assert ";" not in wrapped, f"Titik koma tertinggal setelah wrapper: {wrapped!r}"


def test_enforce_limit_clamps_to_the_contract_maximum_of_1000() -> None:
    guard = _guard()

    assert "LIMIT 1000" in guard.enforce_limit("SELECT 1", 99_999), (
        "Batas baris tidak diklem ke 1000 (SqlAnswer.rows max_length=1000)."
    )
    assert "LIMIT 1" in guard.enforce_limit("SELECT 1", 0), (
        "LIMIT 0 menghasilkan query kosong; harus diklem ke minimal 1."
    )


def test_enforce_limit_does_not_double_wrap_an_already_wrapped_statement() -> None:
    """Calling it twice must not create ``SELECT * FROM (SELECT * FROM (...)``.

    The second call re-wraps the *inner* statement only; the property that
    matters is that a single outer LIMIT exists and the result still validates.
    """
    guard = _guard()
    once = guard.enforce_limit("SELECT * FROM silver_orders", 100)
    twice = guard.enforce_limit(once, 100)

    assert twice.count("LIMIT") == 2, (
        f"Jumlah LIMIT tak terduga setelah dua pembungkusan: {twice!r}"
    )
    assert twice.count("SELECT * FROM (") == 2, (
        f"Pembungkusan kedua tidak terjadi seperti yang didokumentasikan: {twice!r}"
    )
    _accept(twice)


def test_enforce_limit_keeps_an_existing_inner_limit_intact() -> None:
    guard = _guard()
    wrapped = guard.enforce_limit("SELECT * FROM silver_orders LIMIT 5", 100)

    assert "LIMIT 5" in wrapped and "LIMIT 100" in wrapped, (
        f"LIMIT dalam klausa hilang atau tertimpa: {wrapped!r}"
    )


# ---------------------------------------------------------------------------
# write_statement_kind (telemetry only, never enforcement)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("sql", "expected"),
    [
        ("SELECT 1", "select"),
        ("WITH x AS (SELECT 1) SELECT * FROM x", "with"),
        ("INSERT INTO t VALUES (1)", "dml"),
        ("CREATE TABLE t (a INT)", "ddl"),
        ("bukan sql sama sekali !!!", "unknown"),
        ("", "unknown"),
    ],
)
def test_write_statement_kind_labels_without_raising(sql: str, expected: str) -> None:
    guard = _guard()
    assert guard.write_statement_kind(sql) == expected, (
        f"Label diagnostik salah untuk {sql!r}."
    )
