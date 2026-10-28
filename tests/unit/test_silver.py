"""FR-022 (Silver: cast, trim, null-normalise, dedup, validate -> quarantine)
and FR-023 (rows_in == rows_out + quarantined).

The *invariant* is the important test: if ``rows_in != rows_out + quarantined``
a row vanished silently and the operator can never know. Polars is used
directly (no object storage, no DuckDB, no Postgres).

If Polars is missing in the test environment the whole module is skipped with a
reason, never silently passing.
"""

from __future__ import annotations

import pytest

pl = pytest.importorskip("polars", reason="polars tidak terpasang: tes Silver tidak dapat dijalankan.")


def _silver():
    from worker_app import silver

    return silver


def _rules(*rule_dicts: dict) -> list[dict]:
    return list(rule_dicts)


def _rule(kind: str, column: str, *, rule_id: str = "r", **params) -> dict:
    return {
        "rule_id": rule_id,
        "kind": kind,
        "column": column,
        "params": params,
        "severity": "error",
    }


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture()
def bronze_frame():
    """A Bronze-like frame: strings only (Bronze is schema-loose), plus meta cols."""
    return pl.DataFrame(
        {
            "nama": ["  Budi ", "SITI", "", "budi", "  "],
            "umur": ["17", "tidak-angka", "25", "17", "30"],
            "kota": ["Jakarta", "Bandung", "JAKARTA", "Jakarta", "Surabaya"],
            "_source_id": ["s1"] * 5,
            "_received_at": ["2026-10-20T00:00:00+00:00"] * 5,
            "_batch_id": ["b1"] * 5,
            "_row_index": [0, 1, 2, 3, 4],
            "_row_hash": ["h1", "h2", "h3", "h4", "h5"],
        }
    )


# ---------------------------------------------------------------------------
# Normalisation: trim + null tokens
# ---------------------------------------------------------------------------
def test_trim_strings_removes_leading_and_trailing_whitespace() -> None:
    silver = _silver()
    frame = pl.DataFrame({"nama": ["  Budi ", "SITI  ", "  ok"]})

    trimmed = silver.trim_strings(frame)
    assert trimmed["nama"].to_list() == ["Budi", "SITI", "ok"], (
        "Trim tidak berjalan: string dengan spasi akan dianggap nilai berbeda."
    )


@pytest.mark.parametrize("token", ["", "NULL", "None", "nan", "N/A", "-", "nil", "  "])
def test_normalize_nulls_maps_every_null_token_to_null(token: str) -> None:
    silver = _silver()
    frame = pl.DataFrame({"kolom": [token, "nilai-nyata"]})

    normalised = silver.normalize_nulls(frame)
    values = normalised["kolom"].to_list()

    assert values[0] is None, (
        f"Token {token!r} tidak dinormalisasi menjadi null; NULL_TOKENS tidak konsisten."
    )
    assert values[1] == "nilai-nyata", "Nilai nyata ikut terhapus oleh normalisasi null."


def test_null_tokens_set_is_the_documented_one() -> None:
    silver = _silver()
    expected = {"", "null", "none", "nan", "na", "n/a", "-", "nil"}
    assert silver.NULL_TOKENS == expected, (
        f"NULL_TOKEN berubah: {silver.NULL_TOKENS}. Setiap perubahan mengubah data silver."
    )


# ---------------------------------------------------------------------------
# Type cast failure -> quarantine (reason ``type_mismatch``)
# ---------------------------------------------------------------------------
def test_cast_failure_quarantines_row_with_type_mismatch_reason() -> None:
    silver = _silver()
    frame = pl.DataFrame({"umur": ["17", "abc", "30"]})
    # ``cast_columns`` is non-strict: a bad value becomes null, it is not dropped.
    casted, failures = silver.cast_columns(frame, {"umur": "int"})
    assert failures == [], f"Cast melaporkan kolom hilang padahal ada: {failures}"
    assert casted["umur"].to_list()[1] is None, (
        "Nilai 'abc' tidak menjadi null setelah cast(long) gagal."
    )

    result = silver.build_silver(
        pl.DataFrame(
            {
                "umur": ["17", "abc", "30"],
                "_unknown_col": ["a", "b", "c"],
            }
        ),
        casts={"umur": "int"},
        rules=_rules(_rule("not_null", "umur", rule_id="umur_wajib")),
    )

    assert result.rows_out == 2, (
        f"Baris valid yang lolos = {result.rows_out}, harusnya 2."
    )
    assert len(result.quarantined) == 1
    codes = [row["_reason_code"] for row in result.quarantined]
    assert codes == ["missing_required_value"], (
        f"Alasan karantina salah: {codes}. Cast gagal -> null -> aturan not_null."
    )


def test_type_mismatch_reason_code_used_for_the_type_rule() -> None:
    """The PRD calls the reason ``type_mismatch``: the ``type`` rule must emit it."""
    silver = _silver()
    frame = pl.DataFrame({"umur": ["17", "abc", "30"], "kota": ["A", "B", "C"]})

    result = silver.build_silver(
        frame,
        rules=_rules(_rule("type", "umur", rule_id="umur_int", dtype="int")),
    )

    assert result.reasons.get("type_mismatch") == 1, (
        f"Kode alasan 'type_mismatch' tidak dihasilkan: {result.reasons}"
    )
    assert result.rows_out == 2


def test_missing_cast_column_is_a_configuration_error_not_a_silent_pass() -> None:
    silver = _silver()
    frame = pl.DataFrame({"ada": ["1"]})

    # Casting a column that does not exist must raise: silently skipping would
    # produce a "successful" run with the wrong schema.
    with pytest.raises(ValueError) as excinfo:
        silver.build_silver(frame, casts={"tidak_ada": "int"})
    assert "tidak_ada" in str(excinfo.value), (
        "Pesan galat tidak menyebut kolom yang hilang."
    )


# ---------------------------------------------------------------------------
# Dedup by _row_hash
# ---------------------------------------------------------------------------
def test_deduplicate_removes_repeated_row_hash() -> None:
    silver = _silver()
    frame = pl.DataFrame(
        {
            "nama": ["a", "a", "b"],
            "_row_hash": ["H", "H", "Z"],
        }
    )

    deduped, removed = silver.deduplicate(frame)

    assert removed == 1, f"Jumlah baris yang dihapus = {removed}, harusnya 1."
    assert deduped.height == 2
    assert deduped["nama"].to_list() == ["a", "b"], (
        "Dedup tidak mempertahankan kemunculan pertama (keep='first')."
    )


def test_build_silver_counts_duplicates_in_reasons() -> None:
    silver = _silver()
    frame = pl.DataFrame(
        {
            "nama": ["budi", "budi", "siti"],
            "_row_hash": ["H1", "H1", "H2"],
        }
    )

    result = silver.build_silver(frame)

    assert result.rows_in == 3
    assert result.rows_out == 2, f"rows_out={result.rows_out}, harusnya 2 setelah dedup."
    assert result.reasons.get("duplicate_row") == 1, (
        f"Duplikat tidak dilaporkan: {result.reasons}"
    )


# ---------------------------------------------------------------------------
# Rule -> reason code mapping (every kind in the contract)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("kind", "params", "column_values", "expected_code"),
    [
        ("not_null", {}, [None, "ok"], "missing_required_value"),
        ("unique", {}, ["a", "a"], "duplicate_value"),
        ("range", {"min": 0, "max": 10}, [50, 5], "out_of_range"),
        ("regex", {"pattern": r"^\d{3}$"}, ["abc", "123"], "pattern_mismatch"),
        ("enum", {"values": ["A", "B"]}, ["Z", "A"], "enum_mismatch"),
    ],
)
def test_each_rule_kind_produces_its_documented_reason_code(
    kind: str, params: dict, column_values: list, expected_code: str
) -> None:
    silver = _silver()
    frame = pl.DataFrame({"kolom": column_values})

    result = silver.build_silver(
        frame, rules=_rules(_rule(kind, "kolom", rule_id=f"{kind}_1", **params))
    )

    assert result.reasons.get(expected_code, 0) == 1, (
        f"Aturan '{kind}' tidak menghasilkan kode '{expected_code}'. "
        f"Kode aktual: {result.reasons}"
    )
    assert result.rows_out == 1, (
        f"Aturan '{kind}' mengarantina {len(result.quarantined)} baris, harusnya 1."
    )


def test_quarantine_rows_carry_reason_detail_and_rule_id() -> None:
    """Operators fix rules from the detail: it must identify the rule and column."""
    silver = _silver()
    frame = pl.DataFrame({"umur": [None, 7]})

    result = silver.build_silver(
        frame, rules=_rules(_rule("not_null", "umur", rule_id="umur_wajib"))
    )

    row = result.quarantined[0]
    assert row["_reason_code"] == "missing_required_value"
    assert "umur_wajib" in row["_reason_detail"], (
        f"Detail tidak menyebut rule_id: {row['_reason_detail']!r}"
    )
    assert "umur" in row["_reason_detail"], (
        f"Detail tidak menyebut kolom: {row['_reason_detail']!r}"
    )
    assert row["_rule_id"] == "umur_wajib"


def test_rule_on_missing_column_quarantines_every_row() -> None:
    """A rule about a column that does not exist is a schema problem, not a pass."""
    silver = _silver()
    frame = pl.DataFrame({"ada": [1, 2, 3]})

    result = silver.build_silver(
        frame, rules=_rules(_rule("not_null", "hilang", rule_id="r_hilang"))
    )

    assert result.rows_out == 0, (
        "Aturan yang menyebut kolom tidak ada DILULUSKAN: data tanpa skema benar masuk silver."
    )
    assert len(result.quarantined) == 3


def test_warn_severity_never_quarantines() -> None:
    silver = _silver()
    frame = pl.DataFrame({"kolom": [None, "ok"]})
    warn_rule = {**_rule("not_null", "kolom", rule_id="lembut"), "severity": "warn"}

    result = silver.build_silver(frame, rules=[warn_rule])

    assert result.rows_out == 2, "Aturan severity='warn' mengarantina baris (harus tidak)."
    assert result.quarantined == []


def test_first_failing_rule_wins_so_a_row_is_quarantined_once() -> None:
    silver = _silver()
    frame = pl.DataFrame({"a": [None], "b": ["x"]})

    result = silver.build_silver(
        frame,
        rules=_rules(
            _rule("not_null", "a", rule_id="r1"),
            _rule("regex", "b", rule_id="r2", pattern=r"^\d+$"),
        ),
    )

    assert len(result.quarantined) == 1, (
        f"Baris yang gagal dua aturan dikarantina {len(result.quarantined)} kali."
    )
    assert result.quarantined[0]["_reason_code"] == "missing_required_value", (
        "Alasan yang tersimpan bukan aturan pertama yang gagal."
    )


# ---------------------------------------------------------------------------
# THE INVARIANT: rows_in == rows_out + quarantined (+ deduped)
# ---------------------------------------------------------------------------
def test_build_silver_invariant_rows_in_equals_out_plus_quarantine(bronze_frame) -> None:
    silver = _silver()

    result = silver.build_silver(
        bronze_frame,
        casts={"umur": "int"},
        rules=_rules(
            _rule("not_null", "nama", rule_id="nama_wajib"),
            _rule("range", "umur", rule_id="umur_wajar", min=0, max=120),
        ),
    )

    quarantined = len(result.quarantined)
    deduped = result.reasons.get("duplicate_row", 0)
    accounted = result.rows_out + quarantined + deduped

    assert result.rows_in == 5, "rows_in salah: fixture punya 5 baris."
    assert accounted == result.rows_in, (
        f"INVARIAN FR-023 GAGAL: rows_in={result.rows_in} tetapi "
        f"rows_out({result.rows_out}) + quarantined({quarantined}) + duplikat({deduped}) "
        f"= {accounted}. Ada baris yang hilang tanpa alasan."
    )


def test_build_silver_invariant_holds_with_no_rules(bronze_frame) -> None:
    silver = _silver()

    result = silver.build_silver(bronze_frame)

    assert result.quarantined == [], "Tanpa aturan tidak boleh ada karantina."
    assert result.rows_in == result.rows_out + result.reasons.get("duplicate_row", 0)


def test_build_silver_reports_schema_of_passing_rows(bronze_frame) -> None:
    silver = _silver()

    result = silver.build_silver(bronze_frame, casts={"umur": "int"})
    names = {entry["name"] for entry in result.schema}

    assert "nama" in names and "umur" in names, f"Skema keluaran tidak lengkap: {names}"
    assert "_row_hash" in names, (
        "Kolom meta _row_hash hilang dari silver: dedup lintas batch tidak mungkin."
    )
    umur_dtype = next(e["dtype"] for e in result.schema if e["name"] == "umur")
    assert "Int64" in umur_dtype, f"Cast ke int tidak tercermin di skema: {umur_dtype}"


# ---------------------------------------------------------------------------
# Business columns vs metadata
# ---------------------------------------------------------------------------
def test_business_columns_excludes_bronze_metadata() -> None:
    silver = _silver()
    frame = pl.DataFrame(
        {"nama": ["a"], "_source_id": ["s"], "_row_hash": ["h"], "_batch_id": ["b"]}
    )

    assert silver.business_columns(frame) == ["nama"], (
        "Kolom meta Bronze bocor ke kolom bisnis: profiler akan menganalisis _row_hash."
    )


def test_empty_batch_produces_empty_result_without_crashing() -> None:
    silver = _silver()
    frame = pl.DataFrame({"nama": pl.Series([], dtype=pl.String)})

    result = silver.build_silver(frame, rules=_rules(_rule("not_null", "nama")))

    assert (result.rows_in, result.rows_out, result.quarantined) == (0, 0, [])


def test_regex_rule_without_pattern_raises_configuration_error() -> None:
    silver = _silver()
    frame = pl.DataFrame({"nama": ["a"]})

    with pytest.raises(ValueError):
        silver.build_silver(frame, rules=_rules(_rule("regex", "nama", rule_id="r_regex")))
