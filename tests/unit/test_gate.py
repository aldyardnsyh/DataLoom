"""A0 deterministic quality gate (``apps/worker/app/gate.py``).

The gate is the ONLY thing that decides whether Silver data may become Gold.
PRD A0 defines four rules:
    (a) silver schema invalid               -> FAIL
    (b) quarantine ratio > project threshold -> FAIL
    (c) silver <-> gold reconciliation failed -> FAIL
    (d) a required agent proposal still pending -> FAIL

Every test flips exactly ONE rule so a failure names the culprit. The evidence
strings are asserted to contain the *numbers*, because PRD A0 forbids a gate
decision without structured evidence ("Tidak percaya pada klaim, hanya bukti").

Pure Python: no DB, no Polars, no network.
"""

from __future__ import annotations

import pytest


def _gate():
    from worker_app import gate

    return gate


def _decision(name: str):
    from dataloom_contracts._base import GateDecision

    return GateDecision(name)


def _call(**overrides):
    gate = _gate()
    kwargs = {
        "schema_valid": True,
        "schema_errors": None,
        "rows_out": 95,
        "rows_quarantined": 5,
        "quarantine_threshold": 0.05,
        "reconciliation_passed": True,
        "reconciliation_detail": {"passed": True, "checks": []},
        "pending_required_proposals": 0,
    }
    kwargs.update(overrides)
    return gate.evaluate_gate(**kwargs)


def _check(result, rule: str):
    matches = [check for check in result.checks if check.rule == rule]
    assert len(matches) == 1, (
        f"Rule gate '{rule}' muncul {len(matches)} kali; harus tepat satu bukti per aturan."
    )
    return matches[0]


# ---------------------------------------------------------------------------
# Happy path: all four rules pass
# ---------------------------------------------------------------------------
def test_gate_passes_when_all_four_rules_pass() -> None:
    result = _call()

    assert result.decision is _decision("pass"), (
        f"Gate gagal padahal semua aturan lulus. Bukti: "
        f"{[(c.rule, c.passed, c.evidence) for c in result.checks]}"
    )
    assert result.passed is True
    rules = {check.rule for check in result.checks}
    assert rules == {
        "schema_valid",
        "quarantine_ratio",
        "reconciliation",
        "pending_proposals",
    }, f"Set aturan gate tidak lengkap: {rules}"


def test_gate_result_serialises_to_the_ui_checklist_shape() -> None:
    result = _call()
    payload = result.to_checklist()

    assert payload["decision"] == "pass"
    assert len(payload["checks"]) == 4
    for item in payload["checks"]:
        assert set(item) == {"rule", "label", "passed", "evidence"}, (
            f"Bentuk checklist menyimpang dari kontrak UI: {sorted(item)}"
        )
        assert item["label"], "Label checklist kosong: UI tidak bisa menampilkannya."


# ---------------------------------------------------------------------------
# Rule (a) schema invalid
# ---------------------------------------------------------------------------
def test_gate_fails_on_invalid_schema_and_shows_the_error() -> None:
    result = _call(schema_valid=False, schema_errors=["n_clean: kolom tidak ada"])

    assert result.decision is _decision("fail"), "Skema tidak valid tetapi gate PASS."
    check = _check(result, "schema_valid")
    assert check.passed is False
    assert "kolom tidak ada" in check.evidence, (
        f"Bukti skema tidak memuat pesan galat asli: {check.evidence!r}"
    )


def test_schema_check_passes_with_a_neutral_evidence_sentence() -> None:
    result = _call()
    check = _check(result, "schema_valid")

    assert check.passed is True
    assert "galat" in check.evidence.lower(), (
        f"Bukti skema lulus harus menyatakan tidak ada galat: {check.evidence!r}"
    )


# ---------------------------------------------------------------------------
# Rule (b) quarantine ratio above threshold
# ---------------------------------------------------------------------------
def test_gate_fails_when_quarantine_ratio_exceeds_threshold() -> None:
    # 10 quarantined out of 100 = 10% > 5% threshold.
    result = _call(rows_out=90, rows_quarantined=10, quarantine_threshold=0.05)

    assert result.decision is _decision("fail")
    check = _check(result, "quarantine_ratio")
    assert check.passed is False
    assert "10" in check.evidence, (
        f"Bukti rasio tidak memuat jumlah baris karantina: {check.evidence!r}"
    )
    assert "100" in check.evidence, (
        f"Bukti rasio tidak memuat penyebut (total baris): {check.evidence!r}"
    )
    assert "10.00%" in check.evidence, (
        f"Bukti rasio tidak memuat persentase yang bisa diaudit: {check.evidence!r}"
    )


def test_gate_passes_when_ratio_equals_the_threshold_exactly() -> None:
    """The rule is 'ratio <= threshold': equality must PASS, not FAIL."""
    result = _call(rows_out=95, rows_quarantined=5, quarantine_threshold=0.05)

    assert _check(result, "quarantine_ratio").passed is True, (
        "Rasio tepat sama dengan ambang dianggap gagal (harus lulus: '<=')."
    )


def test_quarantine_ratio_with_zero_rows_is_zero_not_a_division_error() -> None:
    result = _call(rows_out=0, rows_quarantined=0, quarantine_threshold=0.05)

    check = _check(result, "quarantine_ratio")
    assert check.passed is True
    assert "0.00%" in check.evidence


def test_quarantine_threshold_is_rendered_from_the_supplied_value() -> None:
    result = _call(rows_out=90, rows_quarantined=10, quarantine_threshold=0.2)

    check = _check(result, "quarantine_ratio")
    assert check.passed is True, "Ambang 20% dengan rasio 10% seharusnya lulus."
    assert "20.00%" in check.evidence, (
        f"Ambang yang dilaporkan bukan nilai yang dipakai: {check.evidence!r}"
    )


# ---------------------------------------------------------------------------
# Rule (c) reconciliation failed / unknown
# ---------------------------------------------------------------------------
def test_gate_fails_when_reconciliation_fails() -> None:
    detail = {
        "passed": False,
        "checks": [
            {"check": "sum_group_counts_equals_silver_rows", "passed": False},
            {"check": "measure_sum_matches_total", "passed": True},
        ],
    }
    result = _call(reconciliation_passed=False, reconciliation_detail=detail)

    assert result.decision is _decision("fail")
    check = _check(result, "reconciliation")
    assert check.passed is False
    assert "sum_group_counts_equals_silver_rows" in check.evidence, (
        f"Bukti rekonsiliasi tidak menyebut pemeriksaan yang gagal: {check.evidence!r}"
    )
    assert "1" in check.evidence, (
        f"Bukti tidak menyebut jumlah pemeriksaan yang gagal: {check.evidence!r}"
    )


def test_missing_reconciliation_does_not_block_by_itself_but_is_not_passed() -> None:
    """No aggregate step yet -> 'not evaluated'. It must not be reported as a pass."""
    result = _call(reconciliation_passed=None, reconciliation_detail=None)

    check = _check(result, "reconciliation")
    assert check.passed is False, (
        "Rekonsiliasi yang belum dievaluasi dilaporkan lulus: gate bisa PROMOTE tanpa bukti."
    )
    assert check.blocking is False, (
        "Rekonsiliasi yang belum ada dijadikan blocking; run tanpa aggregate tidak akan pernah selesai."
    )
    assert "Belum" in check.evidence or "belum" in check.evidence


# ---------------------------------------------------------------------------
# Rule (d) pending proposals
# ---------------------------------------------------------------------------
def test_gate_fails_when_a_required_proposal_is_still_pending() -> None:
    result = _call(pending_required_proposals=2)

    assert result.decision is _decision("fail"), (
        "Usulan agent masih pending tetapi gate PASS: aturan (d) dilanggar."
    )
    check = _check(result, "pending_proposals")
    assert check.passed is False
    assert "2" in check.evidence, (
        f"Bukti tidak menyebut jumlah usulan pending: {check.evidence!r}"
    )


def test_gate_passes_pending_proposals_check_when_none_pending() -> None:
    check = _check(_call(pending_required_proposals=0), "pending_proposals")
    assert check.passed is True
    assert "0" in check.evidence


# ---------------------------------------------------------------------------
# Aggregation of failures
# ---------------------------------------------------------------------------
def test_all_four_rules_can_fail_together_and_each_is_reported() -> None:
    result = _call(
        schema_valid=False,
        schema_errors=["n: gagal"],
        rows_out=10,
        rows_quarantined=90,
        quarantine_threshold=0.05,
        reconciliation_passed=False,
        reconciliation_detail={"checks": [{"check": "x", "passed": False}]},
        pending_required_proposals=1,
    )

    assert result.decision is _decision("fail")
    failing = sorted(c.rule for c in result.checks if not c.passed)
    assert failing == sorted(
        ["schema_valid", "quarantine_ratio", "reconciliation", "pending_proposals"]
    ), f"Tidak semua kegagalan dilaporkan; hanya {failing}."


def test_every_blocking_check_carries_non_empty_evidence() -> None:
    result = _call(rows_out=1, rows_quarantined=99, pending_required_proposals=1)

    for check in result.checks:
        assert check.evidence, f"Check '{check.rule}' tidak punya bukti (PRD A0)."


# ---------------------------------------------------------------------------
# quality_report (A3 shape) - deterministic, no LLM
# ---------------------------------------------------------------------------
def test_quality_report_passes_only_when_nothing_is_quarantined() -> None:
    gate = _gate()

    clean = gate.quality_report(rows_in=100, rows_out=100, rows_quarantined=0, reasons={})
    dirty = gate.quality_report(
        rows_in=100, rows_out=90, rows_quarantined=10, reasons={"missing_required_value": 10}
    )

    assert clean["passed"] is True
    assert dirty["passed"] is False, (
        "QualityReport melaporkan lulus padahal ada baris karantina."
    )
    assert dirty["quarantine_ratio"] == pytest.approx(0.1)
    assert dirty["reasons"] == {"missing_required_value": 10}


def test_quality_report_does_not_divide_by_zero() -> None:
    gate = _gate()
    report = gate.quality_report(rows_in=0, rows_out=0, rows_quarantined=0, reasons={})

    assert report["quarantine_ratio"] == 0.0
