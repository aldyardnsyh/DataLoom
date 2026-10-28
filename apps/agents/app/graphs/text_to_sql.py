"""Text-to-SQL graph (PRD A5) - Phase 5.

Flow (PRD A5, exact)::

    understand -> draft_sql -> validate_sql -> run
        -> (error: repair, max 2) -> explain

The AST guard in ``app.tools.sql_guard`` is the real defence: the prompt merely
asks nicely. Every failure mode maps to an explicit ``AgentOutputStatus``:

* unsafe / non-SELECT SQL      -> ``status: rejected``
* ambiguous question            -> ``status: needs_clarification``
* repair budget exhausted       -> ``status: needs_clarification``
"""

from __future__ import annotations

import uuid
from typing import Any, TypedDict

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.graphs.base import ContractViolation, call_llm, parse_json_object, record_event
from app.llm.budget import BudgetGuard
from app.llm.provider import LLMProvider
from app.prompts.sql import build_sql_messages
from app.tools.schema import allowed_tables, get_schema
from app.tools.sql_guard import UnsafeSql, enforce_limit, validate_readonly_sql
from dataloom_contracts._base import AgentOutputStatus

MAX_REPAIRS = 2


class SqlState(TypedDict, total=False):
    org_id: str
    project_id: str
    agent_run_id: str
    question: str
    allowed: list[str]
    schema: list[dict[str, Any]]
    raw_output: dict[str, Any] | None
    sql: str | None
    status: str
    explanation: str
    assumptions: list[str]
    clarify_question: str | None
    rejected_reason: str | None
    repair_attempts: int
    last_error: str
    rows: list[dict[str, Any]]
    columns: list[str]


async def load_schema(session: AsyncSession, state: SqlState) -> SqlState:
    """Resolve the allowlisted tables/columns for this project (org-scoped)."""
    org_id = uuid.UUID(state["org_id"])
    project_id = uuid.UUID(state["project_id"])
    tables = await allowed_tables(session, org_id=org_id, project_id=project_id)
    schema = await get_schema(session, org_id=org_id, project_id=project_id)
    return {"allowed": sorted(tables), "schema": schema}


async def draft_sql(
    session: AsyncSession,
    state: SqlState,
    *,
    provider: LLMProvider,
    guard: BudgetGuard,
) -> SqlState:
    settings = get_settings()
    messages = build_sql_messages(
        question=state["question"],
        schema=state.get("schema", []),
        allowed_tables=state.get("allowed", []),
        previous_error=state.get("last_error") or None,
    )
    result = await call_llm(
        session=session,
        provider=provider,
        guard=guard,
        org_id=uuid.UUID(state["org_id"]),
        agent_run_id=uuid.UUID(state["agent_run_id"]),
        node="draft_sql",
        messages=messages,
        model=settings.LLM_MODEL_DEFAULT,
        max_tokens=800,
        attempt=int(state.get("repair_attempts", 0)),
    )
    payload = parse_json_object(result.response.content)

    status_raw = str(payload.get("status", "ok")).lower()
    if status_raw not in {"ok", "needs_clarification", "rejected"}:
        status_raw = "ok"

    await record_event(
        session,
        org_id=uuid.UUID(state["org_id"]),
        agent_run_id=uuid.UUID(state["agent_run_id"]),
        step="draft_sql",
        node_name="draft_sql",
        status=status_raw,
        payload={"attempt": int(state.get("repair_attempts", 0))},
    )
    return {
        "raw_output": payload,
        "status": status_raw,
        "sql": (str(payload["sql"]) if payload.get("sql") else None),
        "explanation": str(payload.get("explanation", ""))[:1000],
        "assumptions": [str(item)[:300] for item in (payload.get("assumptions") or [])][:10],
        "clarify_question": (str(payload["clarify_question"])[:500] if payload.get("clarify_question") else None),
        "rejected_reason": (str(payload["rejected_reason"])[:500] if payload.get("rejected_reason") else None),
    }


async def validate_sql(session: AsyncSession, state: SqlState) -> SqlState:
    """AST validation + forced LIMIT. Any failure is a hard stop."""
    if state.get("status") != "ok" or not state.get("sql"):
        return {}
    try:
        validate_readonly_sql(state["sql"], set(state.get("allowed", [])))
        safe_sql = enforce_limit(state["sql"], get_settings().SQL_MAX_ROWS)
    except UnsafeSql as exc:
        await record_event(
            session,
            org_id=uuid.UUID(state["org_id"]),
            agent_run_id=uuid.UUID(state["agent_run_id"]),
            step="validate_sql",
            node_name="validate_sql",
            status="rejected",
            payload={"reason": exc.reason},
        )
        # A blocked query is a REJECTION, not a retry: retrying an unsafe query
        # would just ask the model to obfuscate it.
        return {
            "status": AgentOutputStatus.REJECTED.value,
            "sql": None,
            "rejected_reason": exc.reason,
            "clarify_question": None,
        }

    await record_event(
        session,
        org_id=uuid.UUID(state["org_id"]),
        agent_run_id=uuid.UUID(state["agent_run_id"]),
        step="validate_sql",
        node_name="validate_sql",
        status="ok",
        payload={},
    )
    return {"sql": safe_sql}


def after_validate(state: SqlState) -> str:
    if state.get("status") in {AgentOutputStatus.REJECTED.value, AgentOutputStatus.NEEDS_CLARIFICATION.value}:
        return "explain"
    if not state.get("sql"):
        return "repair" if int(state.get("repair_attempts", 0)) < MAX_REPAIRS else "explain"
    return "run"


def after_run(state: SqlState) -> str:
    if state.get("rows") is not None:
        return "explain"
    return "repair" if int(state.get("repair_attempts", 0)) < MAX_REPAIRS else "explain"


__all__ = [
    "MAX_REPAIRS",
    "SqlState",
    "after_run",
    "after_validate",
    "draft_sql",
    "load_schema",
    "validate_sql",
]
