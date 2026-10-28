"""Data Quality Guardian (PRD A3) - deterministic checks, LLM only explains.

The pass/fail verdict is computed in Python (same rules as
``apps/worker/app/gate.py``). The model receives only aggregate statistics and
returns a short explanation; its output NEVER changes the verdict.
"""

from __future__ import annotations

import uuid
from typing import Any, TypedDict

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.graphs.base import call_llm, parse_json_object, record_event
from app.llm.budget import BudgetGuard
from app.llm.provider import LLMProvider
from app.prompts.guardian import build_guardian_messages
from dataloom_contracts._base import GateDecision


class GuardianState(TypedDict, total=False):
    org_id: str
    agent_run_id: str
    rows_in: int
    rows_out: int
    rows_quarantined: int
    reasons: dict[str, int]
    threshold: float
    decision: str
    evidence: str
    explanation: str


def evaluate(*, rows_out: int, rows_quarantined: int, threshold: float) -> tuple[str, str]:
    """Deterministic verdict + evidence sentence (mirrors the A0 gate rule b)."""
    total = rows_out + rows_quarantined
    ratio = (rows_quarantined / total) if total else 0.0
    decision = GateDecision.FAIL.value if ratio > threshold else GateDecision.PASS.value
    evidence = (
        f"Rasio karantina {ratio:.2%} dari {total} baris "
        f"(ambang {threshold:.2%}); keputusan deterministik: {decision}."
    )
    return decision, evidence


async def check(session: AsyncSession, state: GuardianState) -> GuardianState:
    decision, evidence = evaluate(
        rows_out=int(state.get("rows_out", 0)),
        rows_quarantined=int(state.get("rows_quarantined", 0)),
        threshold=float(state.get("threshold", 0.05)),
    )
    await record_event(
        session,
        org_id=uuid.UUID(state["org_id"]),
        agent_run_id=uuid.UUID(state["agent_run_id"]),
        step="check",
        node_name="check",
        status=decision,
        payload={"evidence": evidence},
    )
    return {"decision": decision, "evidence": evidence}


async def explain(
    session: AsyncSession,
    state: GuardianState,
    *,
    provider: LLMProvider,
    guard: BudgetGuard,
) -> GuardianState:
    """Optional explanation. Failure here NEVER changes the verdict."""
    settings = get_settings()
    messages = build_guardian_messages(
        reasons=state.get("reasons", {}),
        rows_in=int(state.get("rows_in", 0)),
        rows_out=int(state.get("rows_out", 0)),
        rows_quarantined=int(state.get("rows_quarantined", 0)),
    )
    try:
        result = await call_llm(
            session=session,
            provider=provider,
            guard=guard,
            org_id=uuid.UUID(state["org_id"]),
            agent_run_id=uuid.UUID(state["agent_run_id"]),
            node="explain",
            messages=messages,
            model=settings.LLM_MODEL_SMALL,
            max_tokens=300,
        )
        payload = parse_json_object(result.response.content)
        explanation = str(payload.get("explanation", ""))[:1000]
    except Exception:  # noqa: BLE001 - explanation is best-effort by design
        explanation = "Penjelasan tidak tersedia; keputusan tetap berasal dari aturan deterministik."
    return {"explanation": explanation}


__all__ = ["GuardianState", "check", "evaluate", "explain"]
