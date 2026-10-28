"""Schema Profiler output contract (PRD A1) — the guard over model output.

These tests exercise ``app.graphs.profiler.validate_proposals`` directly: no LLM,
no database. They prove the *closed catalog*, required params, mandatory
evidence and column allowlist are enforced BEFORE anything is persisted.
"""

from __future__ import annotations

import pytest

from app.graphs.profiler import build_evidence_text, detect_date_format, validate_proposals
from app.graphs.base import ContractViolation

pytestmark = pytest.mark.unit

STATS = [
    {
        "name": "tgl",
        "dtype": "String",
        "null_ratio": 0.03,
        "distinct_count": 412,
        "patterns": {"DD/MM/YYYY": 0.972},
    },
    {
        "name": "amount",
        "dtype": "String",
        "null_ratio": 0.11,
        "distinct_count": 900,
        "patterns": {},
    },
]


def _payload(**proposal: object) -> dict:
    base = {
        "proposal_id": "p1",
        "op": "trim",
        "column": "tgl",
        "params": {},
        "evidence": "3,0% nilai kosong",
        "risk": "low",
        "confidence": 0.9,
    }
    base.update(proposal)
    return {"proposals": [base], "anomalies": [], "columns": [], "open_questions": [], "summary": "ok"}


def test_valid_proposal_is_accepted() -> None:
    proposals, anomalies, columns, questions, summary = validate_proposals(_payload(), stats=STATS)
    assert len(proposals) == 1, "Satu usulan valid harus lolos."
    assert proposals[0]["op"] == "trim"
    assert proposals[0]["column"] == "tgl"
    assert anomalies == [] and columns == [] and questions == []
    assert summary == "ok"


def test_unknown_operation_is_rejected() -> None:
    """The catalog is CLOSED: an op outside ``CleaningOp`` invalidates the output."""
    with pytest.raises(ContractViolation) as excinfo:
        validate_proposals(_payload(op="shuffle_rows"), stats=STATS)
    assert any("katalog" in issue for issue in excinfo.value.issues), (
        "Pesan harus menyebut pelanggaran katalog operasi."
    )


def test_parse_date_without_format_is_rejected_when_undetectable() -> None:
    """`amount` has no detectable date pattern, so no format can be inferred."""
    with pytest.raises(ContractViolation) as excinfo:
        validate_proposals(
            _payload(op="parse_date", column="amount", params={}),
            stats=STATS,
        )
    assert any("format" in issue for issue in excinfo.value.issues)


def test_parse_date_gets_deterministic_format_when_pattern_is_clear() -> None:
    """A missing format is filled from pattern detection, never from the model."""
    proposals, *_ = validate_proposals(
        _payload(op="parse_date", column="tgl", params={}),
        stats=STATS,
    )
    assert proposals[0]["params"]["format"] == "DD/MM/YYYY", (
        "Format harus berasal dari deteksi pola deterministik (97,2% cocok)."
    )


def test_proposal_without_evidence_is_rejected() -> None:
    """PRD A1 'Gagal jika: usulan tanpa bukti'."""
    payload = _payload(evidence="")
    # Remove the deterministic facts as well so no evidence can be derived.
    empty_stats = [{"name": "tgl", "dtype": "String", "null_ratio": 0, "distinct_count": 0, "patterns": {}}]
    with pytest.raises(ContractViolation) as excinfo:
        validate_proposals(payload, stats=empty_stats)
    assert any("bukti" in issue for issue in excinfo.value.issues)


def test_unknown_column_is_rejected() -> None:
    with pytest.raises(ContractViolation) as excinfo:
        validate_proposals(_payload(column="kolom_tidak_ada"), stats=STATS)
    assert any("tidak dikenal" in issue for issue in excinfo.value.issues)


def test_duplicate_operation_on_same_column_is_deduplicated() -> None:
    payload = {
        "proposals": [
            {**_payload()["proposals"][0], "proposal_id": "p1"},
            {**_payload()["proposals"][0], "proposal_id": "p2"},
        ]
    }
    proposals, *_ = validate_proposals(payload, stats=STATS)
    assert len(proposals) == 1, "Operasi yang sama pada kolom yang sama tidak boleh duplikat."


def test_evidence_uses_statistics_not_model_wording() -> None:
    """Numbers in the evidence must come from Python-computed statistics."""
    entry = {"column": "tgl", "evidence": "sebagian besar kosong"}
    text = build_evidence_text(entry, {item["name"]: item for item in STATS})
    assert "3.0% nilai kosong" in text or "3.0%" in text, (
        f"Bukti harus memuat angka statistik asli, bukan hanya klaim model: {text!r}"
    )
    assert "sebagian besar kosong" in text, "Kutipan model tetap disertakan setelah fakta."


def test_detect_date_format_prefers_detected_pattern() -> None:
    assert detect_date_format({"patterns": {"DD/MM/YYYY": 0.9}}) == "DD/MM/YYYY"
    assert detect_date_format({"patterns": {"YYYY-MM-DD": 0.8}}) == "YYYY-MM-DD"
    assert detect_date_format({"patterns": {}}) is None


def test_destructive_risk_is_preserved() -> None:
    proposals, *_ = validate_proposals(_payload(op="drop_if", params={"condition": {"column": "tgl", "op": "is_null"}}, risk="destructive"), stats=STATS)
    assert proposals[0]["risk"] == "destructive", (
        "Usulan destruktif harus tetap ditandai agar tidak pernah auto-apply."
    )
