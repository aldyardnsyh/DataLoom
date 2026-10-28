"""Pipeline Builder graph (PRD A2) - Phase 4 completes the UI wiring.

Flow (PRD A2, exact)::

    understand -> clarify? (interrupt) -> draft_dag -> validate_dag
        -> [invalid: repair, max 2] -> interrupt(approval) -> save_version

The deterministic validator (``PipelineDAG``) is the ONLY gate before a draft
can be shown. The model never executes a pipeline.
"""

from __future__ import annotations

import uuid
from typing import Any, TypedDict

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.graphs.base import ContractViolation, call_llm, parse_json_object, record_event
from app.llm.budget import BudgetGuard
from app.llm.provider import LLMProvider
from app.prompts.builder import build_builder_messages
from app.prompts.wrapping import wrap_untrusted_data
from app.tools.schema import list_datasets
from dataloom_contracts.pipelines import PipelineDAG

MAX_REPAIRS = 2


class BuilderState(TypedDict, total=False):
    org_id: str
    project_id: str
    agent_run_id: str
    description: str
    datasets: list[dict[str, Any]]
    schemas: list[dict[str, Any]]
    raw_output: dict[str, Any] | None
    dag: dict[str, Any] | None
    issues: list[dict[str, Any]]
    repair_attempts: int
    assumptions: list[dict[str, Any]]
    open_questions: list[str]
    name: str
    description_out: str


async def load_context(session: AsyncSession, state: BuilderState) -> BuilderState:
    """Collect the datasets/schemas the builder is allowed to reference."""
    org_id = uuid.UUID(state["org_id"])
    project_id = uuid.UUID(state["project_id"])
    datasets = await list_datasets(session, org_id=org_id, project_id=project_id)
    schemas = [
        {"dataset": dataset.name, "columns": [column for column in dataset.columns]}
        for dataset in datasets
    ]
    return {
        "datasets": [dataset.as_prompt_dict() for dataset in datasets],
        "schemas": schemas,
    }


async def draft_dag(
    session: AsyncSession,
    state: BuilderState,
    *,
    provider: LLMProvider,
    guard: BudgetGuard,
) -> BuilderState:
    """One LLM call producing a draft DAG."""
    settings = get_settings()
    previous_error = (
        "; ".join(str(issue.get("msg", "")) for issue in state.get("issues", []))[:1000]
        if state.get("issues")
        else None
    )
    messages = build_builder_messages(
        description=wrap_untrusted_data(state["description"], "user_description"),
        datasets=state.get("datasets", []),
        schemas=state.get("schemas", []),
        previous_error=previous_error,
    )
    result = await call_llm(
        session=session,
        provider=provider,
        guard=guard,
        org_id=uuid.UUID(state["org_id"]),
        agent_run_id=uuid.UUID(state["agent_run_id"]),
        node="draft_dag",
        messages=messages,
        model=settings.LLM_MODEL_DEFAULT,
        max_tokens=2500,
        attempt=int(state.get("repair_attempts", 0)),
    )
    payload = parse_json_object(result.response.content)
    await record_event(
        session,
        org_id=uuid.UUID(state["org_id"]),
        agent_run_id=uuid.UUID(state["agent_run_id"]),
        step="draft_dag",
        node_name="draft_dag",
        status="ok",
        payload={"attempt": int(state.get("repair_attempts", 0))},
    )
    return {
        "raw_output": payload,
        "name": str(payload.get("name", "Pipeline baru"))[:120],
        "description_out": str(payload.get("description", ""))[:500],
        "assumptions": payload.get("assumptions") if isinstance(payload.get("assumptions"), list) else [],
        "open_questions": [
            str(question)[:300]
            for question in (payload.get("open_questions") or [])
            if str(question).strip()
        ],
    }


def validate_dag_payload(raw: dict[str, Any]) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """Run the SAME validator the API and UI use. Never trust the model."""
    dag_payload = raw.get("dag")
    if not isinstance(dag_payload, dict):
        return None, [{"loc": ["dag"], "msg": "Payload tidak memuat 'dag'."}]
    try:
        dag = PipelineDAG.model_validate(dag_payload)
    except ValidationError as exc:
        issues = [
            {"loc": list(error.get("loc", ())), "msg": error.get("msg", "invalid")}
            for error in exc.errors()
        ]
        return None, issues
    return dag.model_dump(mode="json", by_alias=True), []


async def validate_dag_node(session: AsyncSession, state: BuilderState) -> BuilderState:
    dag, issues = validate_dag_payload(state.get("raw_output") or {})
    await record_event(
        session,
        org_id=uuid.UUID(state["org_id"]),
        agent_run_id=uuid.UUID(state["agent_run_id"]),
        step="validate_dag",
        node_name="validate_dag",
        status="ok" if dag else "invalid",
        payload={"issues": issues[:6]},
    )
    if dag is not None:
        return {"dag": dag, "issues": []}
    return {
        "issues": issues,
        "repair_attempts": int(state.get("repair_attempts", 0)) + 1,
    }


def after_validate(state: BuilderState) -> str:
    if state.get("dag"):
        return "approval"
    if int(state.get("repair_attempts", 0)) > MAX_REPAIRS:
        return "needs_human"
    return "repair"


__all__ = [
    "MAX_REPAIRS",
    "BuilderState",
    "after_validate",
    "draft_dag",
    "load_context",
    "validate_dag_node",
    "validate_dag_payload",
]

