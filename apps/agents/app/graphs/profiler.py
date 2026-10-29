"""Schema Profiler graph (PRD A1) - the core Phase 3 deliverable.

Graph (PRD A1, exact order)::

    load_stats -> infer_schema -> propose_rules -> validate_proposals
        -> [router] --invalid & retry<2--> propose_rules
                   --invalid & retry>=2--> needs_human
                   --valid--> interrupt(approval) -> persist

Safety invariants (ADR-003):
1. The model may ONLY emit operations from the closed ``CleaningOp`` catalog;
   required params are checked with the same rules the worker executes.
2. Every proposal MUST carry a non-empty ``evidence`` string built from the
   deterministic statistics - "usulan tanpa bukti" invalidates the payload.
3. ``risk == destructive`` proposals are never auto-applied: they require an
   explicit per-proposal approval and block ``apply_approved_proposals`` while
   still pending.
4. Every data insertion into a prompt goes through ``wrap_untrusted_data``.
5. Invalid output is retried at most ``AGENT_MAX_RETRIES`` (2) times; afterwards
   the run ends as ``insufficient_evidence`` with ``open_questions`` filled in.
"""

from __future__ import annotations

import re
import uuid
from typing import Any, TypedDict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.models import AgentProposal, Artifact, Dataset
from app.graphs.base import (
    ContractViolation,
    call_llm,
    parse_json_object,
    record_event,
    with_timeout,
)
from app.llm.budget import BudgetGuard
from app.llm.provider import LLMProvider
from app.prompts.profiler import build_profiler_messages
from app.prompts.wrapping import wrap_untrusted_data
from app.tools import storage
from app.tools.sampling import render_rows_csv, sample_for_analysis, sample_for_prompt
from app.tools.stats import profile_frame
from dataloom_contracts._base import (
    AgentOutputStatus,
    AgentRunStatus,
    CleaningOp,
    ProposalKind,
    ProposalStatus,
    RiskLevel,
)
from dataloom_contracts.agents import CleaningProposal, ProfileReport
from dataloom_contracts.pipelines import CleanOpSpec

MAX_PROMPT_ATTEMPTS = 3  # 1 attempt + AGENT_MAX_RETRIES(2)

#: Params required per operation (mirrors ``CleanOpSpec``).
REQUIRED_PARAMS: dict[CleaningOp, tuple[str, ...]] = {
    CleaningOp.CAST: ("to_type",),
    CleaningOp.PARSE_DATE: ("format",),
    CleaningOp.REGEX_REPLACE: ("pattern", "replacement"),
    CleaningOp.DROP_IF: ("condition",),
    CleaningOp.FILL_DEFAULT: ("value",),
}

#: Fallback questions used when the agent must refuse. A refusal is only valid
#: when it asks the user something (ProfileReport validator).
REFUSAL_QUESTIONS = [
    "Apakah Anda dapat memberikan contoh nilai yang diharapkan untuk kolom yang bermasalah?",
    "Boleh saya memperoleh sampel data yang lebih besar atau kamus data untuk kolom tersebut?",
]

_DATE_HINT = re.compile(r"(\d{2})[/-](\d{2})[/-](\d{4})")


class ProfilerState(TypedDict, total=False):
    """Small, JSON-serialisable state carried between nodes.

    The Polars frame is deliberately NOT part of the state: it is large and not
    serialisable, so it lives only for the duration of ``load_stats``.
    """

    org_id: str
    project_id: str
    agent_run_id: str
    dataset_id: str
    user_id: str | None
    stats: list[dict[str, Any]]
    sampled_rows: int
    total_rows: int | None
    sample_csv: str
    raw_output: dict[str, Any] | None
    proposals: list[dict[str, Any]]
    anomalies: list[dict[str, Any]]
    columns: list[dict[str, Any]]
    open_questions: list[str]
    summary: str
    invalid_reason: str
    attempts: int


# ---------------------------------------------------------------------------
# Node 1: load_stats (deterministic, no LLM)
# ---------------------------------------------------------------------------
async def load_stats(session: AsyncSession, state: ProfilerState) -> ProfilerState:
    """Read a bounded sample and compute deterministic statistics."""
    settings = get_settings()
    org_id = uuid.UUID(state["org_id"])
    dataset = (
        await session.execute(
            select(Dataset).where(
                Dataset.id == uuid.UUID(state["dataset_id"]), Dataset.org_id == org_id
            )
        )
    ).scalar_one_or_none()
    if dataset is None or not dataset.storage_uri:
        raise ContractViolation("Dataset tidak ditemukan atau belum memiliki data.")

    frame = storage.read_frame(dataset.storage_uri)
    limited = sample_for_analysis(frame, max_rows=settings.SAMPLE_ROWS_MAX)
    stats = [stat.as_prompt_dict() for stat in profile_frame(limited, sample_limit=10)]
    prompt_frame = sample_for_prompt(limited, max_rows=settings.PROMPT_SAMPLE_ROWS)
    sample_csv = render_rows_csv(prompt_frame, max_chars=settings.PROMPT_MAX_CHARS)

    await record_event(
        session,
        org_id=org_id,
        agent_run_id=uuid.UUID(state["agent_run_id"]),
        step="load_stats",
        node_name="load_stats",
        status="ok",
        payload={"rows_analysed": limited.height, "columns": len(stats)},
    )
    return {
        "stats": stats,
        "sampled_rows": limited.height,
        "total_rows": int(dataset.row_count) if dataset.row_count is not None else None,
        "sample_csv": sample_csv,
        "attempts": 0,
    }


# ---------------------------------------------------------------------------
# Node 2+3: infer_schema / propose_rules (one LLM call)
# ---------------------------------------------------------------------------
async def propose_rules(
    session: AsyncSession,
    state: ProfilerState,
    *,
    provider: LLMProvider,
    guard: BudgetGuard,
) -> ProfilerState:
    """Ask the model for a ProfileReport-shaped JSON payload.

    The model sees ONLY deterministic statistics plus at most 50 rows wrapped as
    untrusted data. It never sees a connection string, a storage path or a key.
    """
    settings = get_settings()
    attempt = int(state.get("attempts", 0))

    messages = build_profiler_messages(
        stats=state.get("stats", []),
        row_count=int(state.get("sampled_rows", 0)),
        sample_csv=wrap_untrusted_data(state.get("sample_csv", ""), "sample_rows"),
        previous_error=state.get("invalid_reason") or None,
    )

    result = await call_llm(
        session=session,
        provider=provider,
        guard=guard,
        org_id=uuid.UUID(state["org_id"]),
        agent_run_id=uuid.UUID(state["agent_run_id"]),
        node="propose_rules",
        messages=messages,
        model=settings.LLM_MODEL_DEFAULT,
        max_tokens=2000,
        attempt=attempt,
    )

    payload = parse_json_object(result.response.content)
    await record_event(
        session,
        org_id=uuid.UUID(state["org_id"]),
        agent_run_id=uuid.UUID(state["agent_run_id"]),
        step="infer_schema",
        node_name="infer_schema",
        status="ok",
        payload={"proposals_raw": len(payload.get("proposals", []) or [])},
    )
    return {"raw_output": payload, "attempts": attempt + 1}


# ---------------------------------------------------------------------------
# Node 4: validate_proposals (deterministic gate over model output)
# ---------------------------------------------------------------------------
def build_evidence_text(proposal: dict[str, Any], stats_by_column: dict[str, dict[str, Any]]) -> str:
    """Compose the mandatory evidence string from real statistics.

    The model's own wording is used only if it survives a sanity check; the
    numbers always come from ``stats_by_column`` (computed by Python), so an
    invented percentage cannot enter the report.
    """
    column = str(proposal.get("column", ""))
    stats = stats_by_column.get(column, {})
    model_evidence = str(proposal.get("evidence", "")).strip()

    facts: list[str] = []
    null_ratio = stats.get("null_ratio")
    if isinstance(null_ratio, (int, float)) and float(null_ratio) > 0:
        facts.append(f"{float(null_ratio) * 100:.1f}% nilai kosong")
    distinct = stats.get("distinct_count")
    if isinstance(distinct, int) and distinct > 0:
        facts.append(f"{distinct} nilai unik")
    patterns = stats.get("patterns") or {}
    if isinstance(patterns, dict) and patterns:
        best = max(patterns.items(), key=lambda item: item[1])
        facts.append(f"{best[1] * 100:.1f}% cocok pola {best[0]}")

    fact_text = ", ".join(facts[:3])
    if model_evidence and fact_text:
        return f"{fact_text}. {model_evidence}"[:500]
    if fact_text:
        return fact_text[:500]
    return model_evidence[:500]


def detect_date_format(stats: dict[str, Any]) -> str | None:
    """Guess a date format from the deterministic pattern detection only."""
    patterns = stats.get("patterns") or {}
    if not isinstance(patterns, dict):
        return None
    for pattern in patterns:
        if "DD/MM" in str(pattern) or "d/m" in str(pattern).lower():
            return "DD/MM/YYYY"
        if "YYYY-MM" in str(pattern) or "y-m" in str(pattern).lower():
            return "YYYY-MM-DD"
    return None


def validate_proposals(
    raw: dict[str, Any], *, stats: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[str], str]:
    """Validate the model payload against the contracts.

    Returns ``(proposals, anomalies, columns, open_questions, summary)`` and
    raises ``ContractViolation`` when the payload cannot be salvaged.
    """
    issues: list[str] = []
    proposals: list[dict[str, Any]] = []
    stats_by_column = {str(item.get("name")): item for item in stats}

    raw_proposals = raw.get("proposals")
    if raw_proposals is None:
        raise ContractViolation("Payload tidak memuat 'proposals'.")
    if not isinstance(raw_proposals, list):
        raise ContractViolation("'proposals' harus berupa array.")

    # Deterministic deduplication key so the UI never shows the same rule twice.
    seen: set[tuple[str, str]] = set()

    for index, entry in enumerate(raw_proposals):
        if not isinstance(entry, dict):
            issues.append(f"proposal[{index}] bukan objek")
            continue

        op_value = str(entry.get("op", "")).lower().strip()
        try:
            op = CleaningOp(op_value)
        except ValueError:
            # Closed catalog (PRD A1): unknown op invalidates the payload.
            issues.append(f"proposal[{index}] memakai operasi di luar katalog: {op_value!r}")
            continue

        column = str(entry.get("column", "")).strip()
        if not column:
            issues.append(f"proposal[{index}] tanpa kolom")
            continue
        if column not in stats_by_column:
            issues.append(f"proposal[{index}] menyebut kolom tidak dikenal: {column!r}")
            continue

        params = entry.get("params") if isinstance(entry.get("params"), dict) else {}
        # Fill a missing date format ONLY from deterministic detection.
        if op is CleaningOp.PARSE_DATE and "format" not in params:
            guessed = detect_date_format(stats_by_column[column])
            if guessed:
                params = {**params, "format": guessed}

        missing = [key for key in REQUIRED_PARAMS.get(op, ()) if key not in params]
        if missing:
            issues.append(f"proposal[{index}] ({op.value}) kurang parameter: {missing}")
            continue

        evidence_text = build_evidence_text(entry, stats_by_column)
        if not evidence_text:
            # PRD A1 "Gagal jika: usulan tanpa bukti".
            issues.append(f"proposal[{index}] ({op.value}) tanpa bukti")
            continue

        risk_raw = str(entry.get("risk", "low")).lower().strip()
        try:
            risk = RiskLevel(risk_raw)
        except ValueError:
            risk = RiskLevel.MEDIUM

        try:
            confidence = float(entry.get("confidence", 0.0))
        except (TypeError, ValueError):
            confidence = 0.0

        dedup_key = (op.value, column)
        if dedup_key in seen:
            continue
        seen.add(dedup_key)

        spec = CleanOpSpec(op=op, column=column, params=params, risk=risk)
        proposal = CleaningProposal(
            proposal_id=str(entry.get("proposal_id") or f"p{index + 1}")[:64],
            op=spec.op,
            column=spec.column,
            params=dict(spec.params),
            evidence=evidence_text,
            risk=spec.risk,
            confidence=min(1.0, max(0.0, confidence)),
            rationale=(str(entry.get("rationale")) if entry.get("rationale") else None),
            before_samples=[str(v) for v in (entry.get("before_samples") or [])][:5],
            after_samples=[str(v) for v in (entry.get("after_samples") or [])][:5],
        )
        proposals.append(proposal.model_dump(mode="json"))

    if issues:
        raise ContractViolation("Output agent tidak valid: " + "; ".join(issues[:6]), issues)

    anomalies = raw.get("anomalies") if isinstance(raw.get("anomalies"), list) else []
    columns = raw.get("columns") if isinstance(raw.get("columns"), list) else []
    questions = [str(q)[:300] for q in (raw.get("open_questions") or []) if str(q).strip()]
    summary = str(raw.get("summary", ""))[:1000]

    return proposals, anomalies, columns, questions, summary


async def validate_proposals_node(session: AsyncSession, state: ProfilerState) -> ProfilerState:
    """Validate the last model output; store either proposals or a reason."""
    try:
        proposals, anomalies, columns, questions, summary = validate_proposals(
            state.get("raw_output") or {}, stats=state.get("stats", [])
        )
    except ContractViolation as exc:
        await record_event(
            session,
            org_id=uuid.UUID(state["org_id"]),
            agent_run_id=uuid.UUID(state["agent_run_id"]),
            step="validate_proposals",
            node_name="validate_proposals",
            status="invalid",
            payload={"issues": exc.issues[:6], "attempt": state.get("attempts", 0)},
        )
        return {"invalid_reason": str(exc)}

    if not proposals:
        # Valid shape but nothing usable: refuse to guess (PRD 10.1 rule 2).
        await record_event(
            session,
            org_id=uuid.UUID(state["org_id"]),
            agent_run_id=uuid.UUID(state["agent_run_id"]),
            step="validate_proposals",
            node_name="validate_proposals",
            status="empty",
            payload={},
        )
        return {"invalid_reason": "Tidak ada usulan dengan bukti yang cukup."}

    await record_event(
        session,
        org_id=uuid.UUID(state["org_id"]),
        agent_run_id=uuid.UUID(state["agent_run_id"]),
        step="validate_proposals",
        node_name="validate_proposals",
        status="ok",
        payload={"proposals": len(proposals)},
    )
    return {
        "proposals": proposals,
        "anomalies": anomalies,
        "columns": columns,
        "open_questions": questions,
        "summary": summary,
        "invalid_reason": "",
    }


# ---------------------------------------------------------------------------
# Deterministic router (never an LLM decision, PRD 9.9)
# ---------------------------------------------------------------------------
def after_validate(state: ProfilerState) -> str:
    """Route after validation: retry, escalate, or go to approval."""
    if not state.get("invalid_reason"):
        return "approval"
    if int(state.get("attempts", 0)) >= MAX_PROMPT_ATTEMPTS:
        return "needs_human"
    return "retry"


# ---------------------------------------------------------------------------
# Report assembly + persistence (called by runner / API)
# ---------------------------------------------------------------------------
def build_report(state: ProfilerState, *, dataset_id: uuid.UUID) -> ProfileReport:
    """Assemble the final ``ProfileReport`` contract from graph state."""
    proposals = state.get("proposals") or []
    ok = bool(proposals)
    questions = state.get("open_questions") or ([] if ok else list(REFUSAL_QUESTIONS))
    summary = state.get("summary") or (
        "Agent tidak menemukan usulan yang didukung bukti yang cukup."
        if not ok
        else None
    )

    return ProfileReport(
        status=AgentOutputStatus.OK if ok else AgentOutputStatus.INSUFFICIENT_EVIDENCE,
        dataset_id=dataset_id,
        sampled_rows=int(state.get("sampled_rows", 0)),
        total_rows=state.get("total_rows"),
        columns=state.get("columns") or [],
        anomalies=state.get("anomalies") or [],
        cleaning_proposals=[CleaningProposal.model_validate(p) for p in proposals],
        open_questions=questions,
        summary=summary,
    )


async def persist_proposals(
    session: AsyncSession,
    *,
    state: ProfilerState,
    dataset_id: uuid.UUID,
    approved_ids: list[str],
) -> list[AgentProposal]:
    """Create ``agent_proposals`` rows for a completed profiler run.

    Every row starts as ``pending``; ids approved during the interrupt become
    ``approved``. Nothing touches a pipeline here - see
    :func:`apply_approved_proposals`.
    """
    org_id = uuid.UUID(state["org_id"])
    agent_run_id = uuid.UUID(state["agent_run_id"])
    created: list[AgentProposal] = []

    for proposal in state.get("proposals", []):
        proposal_id = str(proposal.get("proposal_id"))
        approved = proposal_id in approved_ids
        row = AgentProposal(
            org_id=org_id,
            agent_run_id=agent_run_id,
            kind=ProposalKind.CLEANING_RULES.value,
            title=f"{proposal['op']} pada kolom {proposal['column']}",
            payload_json=proposal,
            evidence_json={"evidence": proposal.get("evidence", "")},
            risk=str(proposal.get("risk", "low")),
            confidence=float(proposal.get("confidence", 0.0)),
            status=ProposalStatus.APPROVED.value if approved else ProposalStatus.PENDING.value,
        )
        session.add(row)
        created.append(row)

    await session.flush()

    # Persist the report as an artifact so the UI can reload it later.
    report = build_report(state, dataset_id=dataset_id)
    session.add(
        Artifact(
            org_id=org_id,
            project_id=uuid.UUID(state["project_id"]),
            type="profile_report",
            version=1,
            producing_agent="schema_profiler",
            agent_run_id=agent_run_id,
            validation_status="valid" if report.status is AgentOutputStatus.OK else "draft",
            payload_json=report.model_dump(mode="json"),
        )
    )
    await session.flush()
    return created


async def apply_approved_proposals(
    session: AsyncSession, *, agent_run_id: uuid.UUID, org_id: uuid.UUID
) -> list[dict[str, Any]]:
    """Return approved cleaning operations in DAG param shape.

    Refuses while a ``destructive`` proposal is still ``pending``: those must be
    decided individually (PRD A1: never delete data without approval).
    """
    rows = list(
        (
            await session.execute(
                select(AgentProposal).where(
                    AgentProposal.agent_run_id == agent_run_id,
                    AgentProposal.org_id == org_id,
                )
            )
        ).scalars()
    )
    blocking = [
        row
        for row in rows
        if row.risk == RiskLevel.DESTRUCTIVE.value and row.status == ProposalStatus.PENDING.value
    ]
    if blocking:
        raise ContractViolation(
            "Ada usulan destruktif yang belum diputuskan; tidak dapat diterapkan."
        )

    approved = [row for row in rows if row.status == ProposalStatus.APPROVED.value]
    return [
        {
            "op": row.payload_json.get("op"),
            "column": row.payload_json.get("column"),
            "params": row.payload_json.get("params", {}),
            "risk": row.risk,
        }
        for row in approved
    ]


def set_run_status(agent_run: Any, status: AgentRunStatus) -> None:
    """Small helper so the runner does not stringify statuses by hand."""
    agent_run.status = status.value


__all__ = [
    "MAX_PROMPT_ATTEMPTS",
    "ProfilerState",
    "after_validate",
    "apply_approved_proposals",
    "build_evidence_text",
    "build_report",
    "detect_date_format",
    "load_stats",
    "persist_proposals",
    "propose_rules",
    "set_run_status",
    "validate_proposals",
    "validate_proposals_node",
]

