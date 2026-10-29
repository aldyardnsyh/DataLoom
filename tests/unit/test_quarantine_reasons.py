"""Quarantine must keep the specific rule code, not overwrite it."""

from __future__ import annotations

import polars as pl


def test_build_quarantine_frame_preserves_rule_specific_codes() -> None:
    from app import quarantine, silver

    frame = pl.DataFrame({"email": ["a@example.test", None, "b@example.test"]})
    rule = {
        "rule_id": "email_required",
        "kind": "not_null",
        "column": "email",
        "params": {},
        "severity": "error",
    }

    _passing, rejected, counts = silver.apply_rules(frame, [rule])
    q_frame = quarantine.build_quarantine_frame(
        rejected,
        reason_code="validation_failed",
        reason_detail="rule violation",
    )

    assert counts == {"missing_required_value": 1}
    assert q_frame["_reason_code"].to_list() == ["missing_required_value"]
    assert q_frame["_rule_id"].to_list() == ["email_required"]
    assert q_frame["_reason_detail"].to_list() == ["email_required: nilai kosong pada 'email'"]
