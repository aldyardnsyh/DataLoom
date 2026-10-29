"""Self-Healing graph (PRD A4, P1).

Flow (PRD A4, exact)::

    collect_evidence -> diagnose -> propose_fix -> validate_dag
        -> interrupt(approval) -> new_version -> requeue

Boundaries enforced here:
- Never changes the OLD version; a fix produces a NEW pipeline version.
- Never re-runs the pipeline itself; it only requeues after approval.
- At most 2 proposals per failed run.
- If evidence is insufficient -> ``status: insufficient_evidence`` with questions.
"""

from __future__ import annotations

import uuid
from typing import Any, TypedDict

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.models import AgentProposal, Artifact
from app.graphs.base import call_llm, parse_json_object, record_event
from app.graphs.pipeline_builder import validate_dag_payload
from app.llm.budget import BudgetGuard
from app.llm.provider import LLMProvider
from app.prompts.healing import build_healing_messages
from app.prompts.wrapping import wrap_untrusted_data
from app.tools.logs import collect_run_evidence
from dataloom_contracts._base import (
    AgentOutputStatus,
    ArtifactType,
    DiagnosisCategory,
    ProposalKind,
    ProposalStatus,
)

MAX_PROPOSALS_PER_RUN = 2


class HealingState(TypedDict, total=False):
    org_id: str
    project_id: str
    agent_run_id: str
    run_id: str
    evidence: dict[str, Any]
    diagnosis: dict[str, Any] | None
    status: str
    category: str
    summary: str
    fix: dict[str, Any] | None
    issues: list[dict[str, Any]]
    open_questions: list[str]
    last_error: str
    repair_attempts: int


async def collect_evidence(session: AsyncSession, state: HealingState) -> HealingState:
    """Read run node errors + redacted logs. Never throws on missing data.

    Every excerpt is wrapped with ``wrap_untrusted_data`` (PRD 10.1 rule 1): log
    lines and node error messages are written by more than one writer (driver,
    library, an upstream system the tenant chose) and are therefore DATA, not
    instructions, when they enter the prompt.
    """
    evidence = await collect_run_evidence(
        session, org_id=uuid.UUID(state["org_id"]), run_id=uuid.UUID(state["run_id"])
    )
    # ``RunEvidence`` is a dataclass; render it to plain JSON for the prompt and
    # for the checkpointed state (only redacted, bounded excerpts are kept).
    rendered = {
        "run_id": str(evidence.run_id) if evidence.run_id else None,
        "run_status": evidence.run_status,
        "error_code": evidence.error_code,
        "error_message": wrap_untrusted_data(
            (evidence.error_message or "")[:1000], "run_error_message"
        )
        if evidence.error_message
        else "",
        "items": [item.to_contract_dict() for item in evidence.items][:50],
        "notes": evidence.notes[:20],
    }

    await record_event(
        session,
        org_id=uuid.UUID(state["org_id"]),
        agent_run_id=uuid.UUID(state["agent_run_id"]),
        step="collect_evidence",
        node_name="collect_evidence",
        status="ok",
        payload={"has_evidence": evidence.has_any(), "items": len(rendered["items"])},
    )
    return {"evidence": rendered}


async def diagnose(
    session: AsyncSession,
    state: HealingState,
    *,
    provider: LLMProvider,
    guard: BudgetGuard,
) -> HealingState:
    settings = get_settings()
    messages = build_healing_messages(
        run_evidence=state.get("evidence", {}),
        previous_error=state.get("last_error") or None,
    )
    result = await call_llm(
        session=session,
        provider=provider,
        guard=guard,
        org_id=uuid.UUID(state["org_id"]),
        agent_run_id=uuid.UUID(state["agent_run_id"]),
        node="diagnose",
        messages=messages,
        model=settings.LLM_MODEL_DEFAULT,
        max_tokens=1500,
        attempt=int(state.get("repair_attempts", 0)),
    )
    payload = parse_json_object(result.response.content)

    status = str(payload.get("status", "ok")).lower()
    if status not in {"ok", "insufficient_evidence"}:
        status = "insufficient_evidence"

    category = str(payload.get("category", "bug")).lower()
    if category not in {item.value for item in DiagnosisCategory}:
        category = DiagnosisCategory.BUG.value

    evidence_items = payload.get("evidence") if isinstance(payload.get("evidence"), list) else []
    if status == "ok" and not evidence_items:
        # "Diagnosis tanpa bukti" is not allowed (PRD A4).
        status = "insufficient_evidence"

    await record_event(
        session,
        org_id=uuid.UUID(state["org_id"]),
        agent_run_id=uuid.UUID(state["agent_run_id"]),
        step="diagnose",
        node_name="diagnose",
        status=status,
        payload={"category": category},
    )
    return {
        "diagnosis": payload,
        "status": status,
        "category": category,
        "summary": str(payload.get("summary", ""))[:1000],
        "fix": payload.get("fix") if isinstance(payload.get("fix"), dict) else None,
        "open_questions": [
            str(item)[:300] for item in (payload.get("open_questions") or []) if str(item).strip()
        ],
    }


def after_diagnose(state: HealingState) -> str:
    if state.get("status") == AgentOutputStatus.INSUFFICIENT_EVIDENCE.value:
        return "needs_human"
    return "validate_fix"


async def validate_fix(session: AsyncSession, state: HealingState) -> HealingState:
    """A fix must still produce a VALID DAG (never a broken version)."""
    fix = state.get("fix") or {}
    dag_payload = fix.get("dag")
    if not isinstance(dag_payload, dict):
        # No DAG change proposed: the fix is parameter-only. Accept, but the
        # approval step still needs the human to confirm the concrete change.
        return {"issues": []}
    _, issues = validate_dag_payload({"dag": dag_payload})
    await record_event(
        session,
        org_id=uuid.UUID(state["org_id"]),
        agent_run_id=uuid.UUID(state["agent_run_id"]),
        step="validate_dag",
        node_name="validate_dag",
        status="ok" if not issues else "invalid",
        payload={"issues": issues[:6]},
    )
    return {"issues": issues}


def after_validate_fix(state: HealingState) -> str:
    if not state.get("issues"):
        return "approval"
    return "needs_human"


async def persist_healing_proposal(
    session: AsyncSession, *, state: HealingState
) -> AgentProposal | None:
    """Persist the fix as one ``AgentProposal`` + ``healing_diagnosis`` artifact.

    Mirrors the profiler ``persist_proposals`` pattern: proposal starts as
    ``pending``; the approval interrupt decides later. Cap: at most
    ``MAX_PROPOSALS_PER_RUN`` rows per failed run. Returns ``None`` when there
    is no fix to persist (insufficient evidence path).
    """
    fix = state.get("fix")
    if not isinstance(fix, dict) or not fix:
        return None
    org_id = uuid.UUID(state["org_id"])
    agent_run_id = uuid.UUID(state["agent_run_id"])
    existing = await session.execute(
        select(func.count())
        .select_from(AgentProposal)
        .where(
            AgentProposal.agent_run_id == agent_run_id,
            AgentProposal.org_id == org_id,
            AgentProposal.kind == ProposalKind.FIX_PROPOSAL.value,
        )
    )
    if (existing.scalar() or 0) >= MAX_PROPOSALS_PER_RUN:
        from app.graphs.base import ContractViolation

        raise ContractViolation("Batas 2 usulan per run tercapai.")
    row = AgentProposal(
        org_id=org_id,
        agent_run_id=agent_run_id,
        kind=ProposalKind.FIX_PROPOSAL.value,
        title=str(fix.get("title") or state.get("summary") or "Fix usulan self-healing")[:200],
        payload_json=fix,
        evidence_json={"evidence": state.get("evidence", {}), "diagnosis": state.get("diagnosis")},
        risk="low",
        confidence=0.0,
        status=ProposalStatus.PENDING.value,
    )
    session.add(row)
    await session.flush()
    session.add(
        Artifact(
            org_id=org_id,
            project_id=uuid.UUID(state["project_id"]),
            type=ArtifactType.DIAGNOSIS.value,
            version=1,
            producing_agent="self_healing",
            agent_run_id=agent_run_id,
            validation_status="draft" if state.get("issues") else "valid",
            payload_json={
                "diagnosis": state.get("diagnosis"),
                "category": state.get("category"),
                "summary": state.get("summary"),
                "fix": fix,
                "issues": state.get("issues", []),
                "open_questions": state.get("open_questions", []),
            },
        )
    )
    await session.flush()
    return row


__all__ = [
    "MAX_PROPOSALS_PER_RUN",
    "HealingState",
    "after_diagnose",
    "after_validate_fix",
    "collect_evidence",
    "diagnose",
    "persist_healing_proposal",
    "validate_fix",
]

