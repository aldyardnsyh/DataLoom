"""Agent runner: builds the graph, runs it, and manages ``agent_runs`` rows.

Public API used by the FastAPI backend:

    await runner.start_profiler(org_id=..., project_id=..., dataset_id=..., user_id=...)
    await runner.resume_agent(thread_id=..., approved_proposal_ids=[...])
    await runner.get_report(agent_run_id=..., org_id=...)
    await runner.apply_approved(agent_run_id=..., org_id=...)

Execution is gated by ``AGENT_EXECUTION_ENABLED`` (default false). The default
refuses to call a model, so the API can still create the run and proposals from
a queued placeholder. Set it to true only where the agent worker is allowed to
call the LLM.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.models import AgentProposal, AgentRun, Artifact
from app.db.session import session_scope
from app.graphs import profiler as profiler_graph
from app.graphs import self_healing as healing_graph
from app.graphs.base import ContractViolation, NodeTimeout, checkpointer, record_event
from app.llm import build_provider
from app.llm.budget import BudgetExceeded, BudgetGuard
from dataloom_contracts._base import AgentRunStatus, AgentType, ProposalKind, ProposalStatus
from dataloom_contracts.agents import ProfileReport

logger = logging.getLogger("dataloom.agents.runner")


@dataclass(slots=True)
class StartResult:
    agent_run_id: uuid.UUID
    thread_id: str
    status: str


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


# ---------------------------------------------------------------------------
# Graph construction
# ---------------------------------------------------------------------------
def build_profiler_graph(session: AsyncSession, *, provider: Any, guard: BudgetGuard):
    """Compile the profiler graph with a Postgres checkpointer.

    The async node functions close over ``session``/``provider``/``guard`` so
    the graph has no hidden globals.
    """

    async def node_load_stats(state: profiler_graph.ProfilerState) -> profiler_graph.ProfilerState:
        return await profiler_graph.load_stats(session, state)

    async def node_propose_rules(state: profiler_graph.ProfilerState) -> profiler_graph.ProfilerState:
        return await profiler_graph.propose_rules(
            session, state, provider=provider, guard=guard
        )

    async def node_validate(state: profiler_graph.ProfilerState) -> profiler_graph.ProfilerState:
        return await profiler_graph.validate_proposals_node(session, state)

    async def node_needs_human(state: profiler_graph.ProfilerState) -> profiler_graph.ProfilerState:
        """Terminal refusal: ask the user instead of guessing (PRD 10.1 rule 2)."""
        await record_event(
            session,
            org_id=uuid.UUID(state["org_id"]),
            agent_run_id=uuid.UUID(state["agent_run_id"]),
            step="needs_human",
            node_name="needs_human",
            status="insufficient_evidence",
            payload={"reason": state.get("invalid_reason", "")[:500]},
        )
        return {
            "open_questions": list(profiler_graph.REFUSAL_QUESTIONS),
            "summary": "Agent tidak dapat menyimpulkan aturan pembersihan dari bukti yang ada.",
        }

    async def node_approval(state: profiler_graph.ProfilerState) -> profiler_graph.ProfilerState:
        """Human-in-the-loop gate (FR-031). Nothing is persisted before this."""
        decision = interrupt(
            {
                "kind": "cleaning_rules_approval",
                "summary": state.get("summary", ""),
                "proposals": state.get("proposals", []),
                "instructions": (
                    "Setujui atau tolak setiap usulan. Usulan destruktif wajib "
                    "diputuskan satu per satu."
                ),
            }
        )
        # `decision` is whatever the caller passed to Command(resume=...).
        approved = []
        if isinstance(decision, dict):
            approved = [str(item) for item in decision.get("approved_proposal_ids", [])]
        elif isinstance(decision, list):
            approved = [str(item) for item in decision]
        return {}

    graph = StateGraph(profiler_graph.ProfilerState)
    graph.add_node("load_stats", node_load_stats)
    graph.add_node("propose_rules", node_propose_rules)
    graph.add_node("validate_proposals", node_validate)
    graph.add_node("needs_human", node_needs_human)
    graph.add_node("approval", node_approval)

    graph.add_edge(START, "load_stats")
    graph.add_edge("load_stats", "propose_rules")
    graph.add_edge("propose_rules", "validate_proposals")
    graph.add_conditional_edges(
        "validate_proposals",
        profiler_graph.after_validate,
        {
            "retry": "propose_rules",
            "needs_human": "needs_human",
            "approval": "approval",
        },
    )
    graph.add_edge("needs_human", END)
    graph.add_edge("approval", END)

    return graph.compile(checkpointer=checkpointer())


def build_healing_graph(session: AsyncSession, *, provider: Any, guard: BudgetGuard):
    """Compile the self-healing graph with a Postgres checkpointer."""

    async def node_collect(state: healing_graph.HealingState) -> healing_graph.HealingState:
        return await healing_graph.collect_evidence(session, state)

    async def node_diagnose(state: healing_graph.HealingState) -> healing_graph.HealingState:
        return await healing_graph.diagnose(session, state, provider=provider, guard=guard)

    async def node_validate(state: healing_graph.HealingState) -> healing_graph.HealingState:
        return await healing_graph.validate_fix(session, state)

    async def node_needs_human(state: healing_graph.HealingState) -> healing_graph.HealingState:
        await record_event(
            session,
            org_id=uuid.UUID(state["org_id"]),
            agent_run_id=uuid.UUID(state["agent_run_id"]),
            step="needs_human",
            node_name="needs_human",
            status="insufficient_evidence",
            payload={"issues": (state.get("issues") or [])[:6]},
        )
        return {
            "open_questions": state.get("open_questions") or [
                "Bukti tidak cukup untuk diagnosis yang aman. Jelaskan error terakhir dan node yang gagal."
            ],
        }

    async def node_approval(state: healing_graph.HealingState) -> healing_graph.HealingState:
        decision = interrupt(
            {
                "kind": "healing_fix_approval",
                "summary": state.get("summary", ""),
                "category": state.get("category", ""),
                "fix": state.get("fix"),
                "instructions": (
                    "Setujui atau tolak fix usulan. Fix yang disetujui membuat "
                    "versi pipeline BARU (tidak mengubah versi lama) lalu requeue."
                ),
            }
        )
        approved: list[str] = []
        if isinstance(decision, dict):
            approved = [str(item) for item in decision.get("approved_proposal_ids", [])]
        elif isinstance(decision, list):
            approved = [str(item) for item in decision]
        return {"open_questions": []} if approved is not None else {}

    graph = StateGraph(healing_graph.HealingState)
    graph.add_node("collect_evidence", node_collect)
    graph.add_node("diagnose", node_diagnose)
    graph.add_node("validate_fix", node_validate)
    graph.add_node("needs_human", node_needs_human)
    graph.add_node("approval", node_approval)

    graph.add_edge(START, "collect_evidence")
    graph.add_edge("collect_evidence", "diagnose")
    graph.add_conditional_edges(
        "diagnose",
        healing_graph.after_diagnose,
        {"needs_human": "needs_human", "validate_fix": "validate_fix"},
    )
    graph.add_conditional_edges(
        "validate_fix",
        healing_graph.after_validate_fix,
        {"approval": "approval", "needs_human": "needs_human"},
    )
    graph.add_edge("needs_human", END)
    graph.add_edge("approval", END)

    return graph.compile(checkpointer=checkpointer())


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------
async def create_agent_run(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    agent_type: AgentType,
    input_json: dict[str, Any],
    user_id: uuid.UUID | None,
    target_dataset_id: uuid.UUID | None = None,
    target_run_id: uuid.UUID | None = None,
    budget_tokens: int | None = None,
) -> AgentRun:
    """Insert a ``pending`` agent_run and return it. Concurrency is checked here."""
    settings = get_settings()
    active = list(
        (
            await session.execute(
                select(AgentRun).where(
                    AgentRun.org_id == org_id,
                    AgentRun.status.in_(
                        [
                            AgentRunStatus.PENDING.value,
                            AgentRunStatus.RUNNING.value,
                            AgentRunStatus.WAITING_APPROVAL.value,
                        ]
                    ),
                )
            )
        ).scalars()
    )
    if len(active) >= settings.AGENT_MAX_CONCURRENT_PER_ORG:
        raise ContractViolation(
            f"Batas {settings.AGENT_MAX_CONCURRENT_PER_ORG} run agent konkuren tercapai."
        )

    run = AgentRun(
        org_id=org_id,
        project_id=project_id,
        agent_type=agent_type.value,
        thread_id=f"{agent_type.value}:{uuid.uuid4()}",
        status=AgentRunStatus.PENDING.value,
        budget_tokens=budget_tokens or settings.DEFAULT_PER_RUN_TOKEN_LIMIT,
        input_json=input_json,
        created_by_user_id=user_id,
        target_dataset_id=target_dataset_id,
        target_run_id=target_run_id,
    )
    session.add(run)
    await session.flush()
    return run


async def start_profiler(
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    dataset_id: uuid.UUID,
    user_id: uuid.UUID | None,
) -> StartResult:
    """Create the run and execute the profiler graph up to the approval interrupt."""
    settings = get_settings()

    async with session_scope() as session:
        run = await create_agent_run(
            session,
            org_id=org_id,
            project_id=project_id,
            agent_type=AgentType.SCHEMA_PROFILER,
            input_json={"dataset_id": str(dataset_id)},
            user_id=user_id,
            target_dataset_id=dataset_id,
        )
        agent_run_id = run.id
        thread_id = run.thread_id

        if not settings.AGENT_EXECUTION_ENABLED:
# Execution disabled: do not call a model. The run stays pending until
            # an operator enables execution.
            run.status = AgentRunStatus.PENDING.value
            run.error_code = "execution_disabled"
            run.error_message = "AGENT_EXECUTION_ENABLED=false; run tidak dijalankan."
            await session.flush()
            return StartResult(agent_run_id=agent_run_id, thread_id=thread_id, status=run.status)

        run.status = AgentRunStatus.RUNNING.value
        run.started_at = _now()
        run.current_step = "load_stats"
        await session.flush()

        provider = build_provider()
        guard = BudgetGuard(session, org_id=org_id, agent_run_id=agent_run_id)
        graph = build_profiler_graph(session, provider=provider, guard=guard)

        state: profiler_graph.ProfilerState = {
            "org_id": str(org_id),
            "project_id": str(project_id),
            "agent_run_id": str(agent_run_id),
            "dataset_id": str(dataset_id),
            "user_id": str(user_id) if user_id else None,
        }

        try:
            config = {"configurable": {"thread_id": thread_id}}
            await graph.ainvoke(state, config=config)
            # The graph stops at interrupt(); mark as waiting for approval.
            run.status = AgentRunStatus.WAITING_APPROVAL.value
            run.current_step = "waiting_approval"
            await session.flush()

            # Persist proposals + artifact from the checkpointed state.
            snapshot = await graph.aget_state(config)
            values = dict(snapshot.values or {})
            await profiler_graph.persist_proposals(
                session, state=values, dataset_id=dataset_id, approved_ids=[]
            )
            await session.commit()
            return StartResult(agent_run_id=agent_run_id, thread_id=thread_id, status=run.status)

        except BudgetExceeded as exc:
            run.status = AgentRunStatus.BUDGET_EXCEEDED.value
            run.error_code = "budget_exceeded"
            run.error_message = exc.safe_message[:500]
            run.finished_at = _now()
            await session.commit()
            return StartResult(agent_run_id=agent_run_id, thread_id=thread_id, status=run.status)

        except (ContractViolation, NodeTimeout) as exc:
            run.status = AgentRunStatus.INSUFFICIENT_EVIDENCE.value
            run.error_code = "insufficient_evidence"
            run.error_message = str(exc)[:500]
            run.finished_at = _now()
            await session.commit()
            return StartResult(agent_run_id=agent_run_id, thread_id=thread_id, status=run.status)

        except Exception as exc:  # noqa: BLE001 - persist a safe failure, never crash silently
            run.status = AgentRunStatus.FAILED.value
            run.error_code = "agent_failed"
            run.error_message = type(exc).__name__[:200]
            run.finished_at = _now()
            await session.commit()
            logger.exception("Profiler gagal untuk agent_run %s", agent_run_id)
            return StartResult(agent_run_id=agent_run_id, thread_id=thread_id, status=run.status)
        finally:
            await provider.aclose()


async def start_healing(
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    run_id: uuid.UUID,
    user_id: uuid.UUID | None,
) -> StartResult:
    """Create the run and execute the self-healing graph up to approval."""
    settings = get_settings()

    async with session_scope() as session:
        run = await create_agent_run(
            session,
            org_id=org_id,
            project_id=project_id,
            agent_type=AgentType.SELF_HEALING,
            input_json={"run_id": str(run_id)},
            user_id=user_id,
            target_run_id=run_id,
        )
        agent_run_id = run.id
        thread_id = run.thread_id

        if not settings.AGENT_EXECUTION_ENABLED:
            run.status = AgentRunStatus.PENDING.value
            run.error_code = "execution_disabled"
            run.error_message = "AGENT_EXECUTION_ENABLED=false; run tidak dijalankan."
            await session.flush()
            return StartResult(agent_run_id=agent_run_id, thread_id=thread_id, status=run.status)

        run.status = AgentRunStatus.RUNNING.value
        run.started_at = _now()
        run.current_step = "collect_evidence"
        await session.flush()

        provider = build_provider()
        guard = BudgetGuard(session, org_id=org_id, agent_run_id=agent_run_id)
        graph = build_healing_graph(session, provider=provider, guard=guard)

        state: healing_graph.HealingState = {
            "org_id": str(org_id),
            "project_id": str(project_id),
            "agent_run_id": str(agent_run_id),
            "run_id": str(run_id),
        }

        try:
            config = {"configurable": {"thread_id": thread_id}}
            await graph.ainvoke(state, config=config)
            run.status = AgentRunStatus.WAITING_APPROVAL.value
            run.current_step = "waiting_approval"
            await session.flush()

            snapshot = await graph.aget_state(config)
            values = dict(snapshot.values or {})
            await healing_graph.persist_healing_proposal(session, state=values)
            await session.commit()
            return StartResult(agent_run_id=agent_run_id, thread_id=thread_id, status=run.status)

        except BudgetExceeded as exc:
            run.status = AgentRunStatus.BUDGET_EXCEEDED.value
            run.error_code = "budget_exceeded"
            run.error_message = exc.safe_message[:500]
            run.finished_at = _now()
            await session.commit()
            return StartResult(agent_run_id=agent_run_id, thread_id=thread_id, status=run.status)

        except (ContractViolation, NodeTimeout) as exc:
            run.status = AgentRunStatus.INSUFFICIENT_EVIDENCE.value
            run.error_code = "insufficient_evidence"
            run.error_message = str(exc)[:500]
            run.finished_at = _now()
            await session.commit()
            return StartResult(agent_run_id=agent_run_id, thread_id=thread_id, status=run.status)

        except Exception as exc:  # noqa: BLE001 - persist a safe failure, never crash silently
            run.status = AgentRunStatus.FAILED.value
            run.error_code = "agent_failed"
            run.error_message = type(exc).__name__[:200]
            run.finished_at = _now()
            await session.commit()
            logger.exception("Self-healing gagal untuk agent_run %s", agent_run_id)
            return StartResult(agent_run_id=agent_run_id, thread_id=thread_id, status=run.status)
        finally:
            await provider.aclose()


async def resume_agent(
    *, thread_id: str, approved_proposal_ids: list[str], org_id: uuid.UUID
) -> StartResult:
    """Resume an interrupted graph with the user's decisions (FR-037).

    This is the ONLY path that turns proposals into approved ones; it is called
    after ``agent_proposals`` rows already store the user's decisions.
    """
    async with session_scope() as session:
        run = (
            await session.execute(
                select(AgentRun).where(
                    AgentRun.thread_id == thread_id,
                    AgentRun.org_id == org_id,
                )
            )
        ).scalar_one_or_none()
        if run is None:
            raise ContractViolation("Agent run tidak ditemukan.")
        if get_settings().AGENT_EXECUTION_ENABLED is False:
            run.status = AgentRunStatus.WAITING_APPROVAL.value
            await session.flush()
            return StartResult(agent_run_id=run.id, thread_id=thread_id, status=run.status)

        provider = build_provider()
        guard = BudgetGuard(session, org_id=run.org_id, agent_run_id=run.id)
        if run.agent_type == AgentType.SELF_HEALING.value:
            graph = build_healing_graph(session, provider=provider, guard=guard)
        else:
            graph = build_profiler_graph(session, provider=provider, guard=guard)

        try:
            config = {"configurable": {"thread_id": thread_id}}
            await graph.ainvoke(
                Command(resume={"approved_proposal_ids": approved_proposal_ids}),
                config=config,
            )
            run.status = AgentRunStatus.SUCCEEDED.value
            run.finished_at = _now()
            await session.commit()
            return StartResult(agent_run_id=run.id, thread_id=thread_id, status=run.status)
        except BudgetExceeded as exc:
            run.status = AgentRunStatus.BUDGET_EXCEEDED.value
            run.error_message = exc.safe_message[:500]
            await session.commit()
            return StartResult(agent_run_id=run.id, thread_id=thread_id, status=run.status)
        finally:
            await provider.aclose()


async def apply_healing_fix(
    *, agent_run_id: uuid.UUID, org_id: uuid.UUID
) -> dict[str, Any]:
    """Build the action plan for an approved healing fix (no pipeline DB write).

    BATAS: tabel pipeline/version milik apps/api, bukan apps/agents. Fungsi ini
    hanya memvalidasi + mengembalikan rencana aksi; apps/api yang mengeksekusi
    new_version + requeue. Return: {"pipeline_id", "new_version_needed": True,
    "dag_fix": <dict|None>, "parameter_fix": <dict>}.

    Raises ContractViolation: tidak ada proposal approved, atau dag invalid.
    """
    from app.graphs.pipeline_builder import validate_dag_payload

    async with session_scope() as session:
        proposal = (
            await session.execute(
                select(AgentProposal)
                .where(
                    AgentProposal.agent_run_id == agent_run_id,
                    AgentProposal.org_id == org_id,
                    AgentProposal.kind == ProposalKind.FIX_PROPOSAL.value,
                    AgentProposal.status == ProposalStatus.APPROVED.value,
                )
                .order_by(AgentProposal.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if proposal is None:
            raise ContractViolation("Tidak ada fix proposal yang approved untuk run ini.")
        fix = dict(proposal.payload_json or {})
        dag_payload = fix.get("dag")
        dag_fix: dict[str, Any] | None = None
        if isinstance(dag_payload, dict):
            dag_fix, issues = validate_dag_payload({"dag": dag_payload})
            if dag_fix is None:
                raise ContractViolation(
                    f"DAG fix invalid: {issues[0].get('msg', 'invalid') if issues else 'invalid'}"
                )
        run = (
            await session.execute(
                select(AgentRun).where(
                    AgentRun.id == agent_run_id,
                    AgentRun.org_id == org_id,
                )
            )
        ).scalar_one_or_none()
        if run is None:
            raise ContractViolation("Agent run tidak ditemukan dalam organisasi ini.")
        target = dict(getattr(run, "input_json", None) or {}) if run else {}
        pipeline_id = target.get("pipeline_id") or fix.get("pipeline_id")
        return {
            "pipeline_id": pipeline_id,
            "new_version_needed": True,
            "dag_fix": dag_fix,
            "parameter_fix": {k: v for k, v in fix.items() if k != "dag"},
        }


async def get_report(*, agent_run_id: uuid.UUID, org_id: uuid.UUID) -> ProfileReport | None:
    """Load the persisted ``profile_report`` artifact for an agent run."""
    async with session_scope() as session:
        artifact = (
            await session.execute(
                select(Artifact)
                .where(
                    Artifact.agent_run_id == agent_run_id,
                    Artifact.org_id == org_id,
                    Artifact.type == "profile_report",
                )
                .order_by(Artifact.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if artifact is None or not artifact.payload_json:
            return None
        return ProfileReport.model_validate(artifact.payload_json)


async def propose_apply(
    *, agent_run_id: uuid.UUID, org_id: uuid.UUID
) -> list[dict[str, Any]]:
    """Return approved cleaning ops (raises when a destructive one is pending)."""
    async with session_scope() as session:
        return await profiler_graph.apply_approved_proposals(
            session, agent_run_id=agent_run_id, org_id=org_id
        )


__all__ = [
    "StartResult",
    "apply_healing_fix",
    "build_healing_graph",
    "build_profiler_graph",
    "create_agent_run",
    "get_report",
    "propose_apply",
    "resume_agent",
    "start_healing",
    "start_profiler",
]


