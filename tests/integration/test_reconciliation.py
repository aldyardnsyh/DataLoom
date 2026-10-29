"""Reconciliation evidence: Silver must agree with Gold (FR-023).

Two layers are covered:
1. ``apps/worker/app/reconcile.py`` helper maths.
2. ``apps/worker/app/gold.py`` ``reconcile`` SQL contract (not started here).
   The pure helper shape is asserted; SQL execution requires runtime.
"""

from __future__ import annotations

import pytest

pl = pytest.importorskip("polars", reason="polars wajib untuk uji rekonsiliasi")

pytestmark = pytest.mark.integration


def test_measure_sum_ignores_nulls_and_non_numeric() -> None:
    from app.reconcile import measure_sum

    frame = pl.DataFrame({"amount": [1.0, None, 2.0], "label": ["a", "b", "c"]})
    assert measure_sum(frame, "amount") == pytest.approx(3.0)
    assert measure_sum(frame, "label") == 0.0, "Kolom non-numerik dianggap 0, bukan galat."
    assert measure_sum(frame, "tidak_ada") == 0.0


def test_sums_agree_within_tolerance() -> None:
    from app.reconcile import sums_agree

    assert sums_agree(100.0, 100.0)
    assert sums_agree(100.0, 100.0 + 1e-9)
    assert not sums_agree(100.0, 100.01), "Selisih nyata harus dianggap tidak cocok."


def test_reconciliation_evidence_passes_when_sums_match() -> None:
    from app.reconcile import reconciliation_evidence

    evidence = reconciliation_evidence(
        silver_rows=10,
        gold_rows=3,
        measures={"total": (12.5, 12.5)},
    )
    assert evidence["passed"] is True
    assert all(check["passed"] for check in evidence["checks"])


def test_reconciliation_evidence_fails_when_gold_is_tampered() -> None:
    """A tampered gold sum MUST fail: this is the gate's strongest check."""
    from app.reconcile import reconciliation_evidence

    evidence = reconciliation_evidence(
        silver_rows=10,
        gold_rows=3,
        measures={"total": (12.5, 99.0)},
    )
    assert evidence["passed"] is False
    failing = [check for check in evidence["checks"] if not check["passed"]]
    assert failing, "Harus ada pemeriksaan yang gagal dan dilaporkan sebagai bukti."
    assert failing[0]["check"] == "sum_total"
    assert failing[0]["expected"] == 12.5
    assert failing[0]["actual"] == 99.0


def test_gold_row_count_cannot_exceed_silver_rows() -> None:
    """Gold aggregates can only reduce rows; more rows means a broken aggregate."""
    from app.reconcile import reconciliation_evidence

    evidence = reconciliation_evidence(silver_rows=5, gold_rows=9, measures={})
    assert evidence["passed"] is False, "Gold tidak boleh memiliki baris lebih banyak dari Silver."


@pytest.mark.runtime_required
def test_gold_reconcile_sql_requires_runtime() -> None:
    """SQL-level reconciliation needs a real DuckDB warehouse file.

    The marker keeps this test out of the default run until that file exists.
    """
    pytest.skip("Butuh DuckDB dan file warehouse nyata.")
