"""Deterministic quality gate (A0 supervisor) and post-batch quality report.

The gate is Python rules over structured data — NEVER an LLM decision
(PRD A0 + section 9.1 principle 5). Rules from PRD A0:

(a) silver schema failed validation
(b) quarantine ratio > project threshold (default 5%)
(c) silver <-> gold reconciliation failed
(d) a required agent proposal is still ``pending``
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from dataloom_contracts._base import GateDecision


@dataclass(slots=True)
class GateCheck:
    rule: str
    label: str
    passed: bool
    evidence: str = ""
    blocking: bool = True


@dataclass(slots=True)
class GateResult:
    decision: GateDecision
    checks: list[GateCheck] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return self.decision is GateDecision.PASS

    def to_checklist(self) -> dict[str, Any]:
        """Serialise into the ``GateChecklist``-compatible dict the UI renders."""
        return {
            "decision": self.decision.value,
            "checks": [
                {
                    "rule": c.rule,
                    "label": c.label,
                    "passed": c.passed,
                    "evidence": c.evidence,
                }
                for c in self.checks
            ],
        }


def evaluate_gate(
    *,
    schema_valid: bool,
    schema_errors: list[str] | None,
    rows_out: int,
    rows_quarantined: int,
    quarantine_threshold: float,
    reconciliation_passed: bool | None,
    reconciliation_detail: dict[str, Any] | None,
    pending_required_proposals: int = 0,
) -> GateResult:
    """Evaluate every gate rule and return a pass/fail decision with evidence."""
    checks: list[GateCheck] = []

    checks.append(
        GateCheck(
            rule="schema_valid",
            label="Skema silver valid",
            passed=bool(schema_valid),
            evidence="; ".join(schema_errors or [])[:500] or "Tidak ada galat skema.",
        )
    )

    denominator = rows_out + rows_quarantined
    ratio = (rows_quarantined / denominator) if denominator else 0.0
    checks.append(
        GateCheck(
            rule="quarantine_ratio",
            label=f"Rasio quarantine di bawah {quarantine_threshold:.0%}",
            passed=ratio <= quarantine_threshold,
            evidence=(
                f"{rows_quarantined} dari {denominator} baris di quarantine "
                f"({ratio:.2%}); ambang {quarantine_threshold:.2%}."
            ),
        )
    )

    if reconciliation_passed is None:
        checks.append(
            GateCheck(
                rule="reconciliation",
                label="Rekonsiliasi silver vs gold",
                passed=False,
                evidence="Belum ada langkah agregasi; rekonsiliasi tidak dapat dinilai.",
                blocking=False,
            )
        )
    else:
        detail = reconciliation_detail or {}
        failed = [c for c in detail.get("checks", []) if not c.get("passed")]
        checks.append(
            GateCheck(
                rule="reconciliation",
                label="Rekonsiliasi silver vs gold",
                passed=bool(reconciliation_passed),
                evidence=(
                    "Semua pemeriksaan cocok."
                    if reconciliation_passed
                    else f"{len(failed)} pemeriksaan gagal: "
                    + ", ".join(str(c.get("check")) for c in failed)[:400]
                ),
            )
        )

    checks.append(
        GateCheck(
            rule="pending_proposals",
            label="Tidak ada usulan agent yang menunggu approval",
            passed=pending_required_proposals == 0,
            evidence=f"{pending_required_proposals} usulan menunggu keputusan.",
        )
    )

    blocking_failures = [c for c in checks if c.blocking and not c.passed]
    decision = GateDecision.FAIL if blocking_failures else GateDecision.PASS
    return GateResult(decision=decision, checks=checks)


def quality_report(
    *, rows_in: int, rows_out: int, rows_quarantined: int, reasons: dict[str, int]
) -> dict[str, Any]:
    """Structured quality report (A3 ``QualityReport`` shape).

    The LLM is allowed to *explain* this report later; it may never alter
    ``passed``. Here everything is computed deterministically.
    """
    total = max(rows_in, 1)
    return {
        "rows_in": rows_in,
        "rows_out": rows_out,
        "rows_quarantined": rows_quarantined,
        "quarantine_ratio": round(rows_quarantined / total, 6),
        "reasons": reasons,
        "passed": rows_quarantined == 0,
    }
