"""Text-to-SQL endpoint (FR-033, PRD A5).

The request path is fully synchronous with respect to safety:

    question -> LLM draft -> sqlglot AST validation -> allowlist -> forced LIMIT
             -> DuckDB read-only -> SqlAnswer

Any unsafe draft is REJECTED (never retried into an obfuscated variant). When
the agent worker is disabled the endpoint returns ``status: rejected`` with an
explanatory message instead of inventing SQL.

SAFETY BOUNDARY (single source of truth): ``app.services.query_service``.
This module used to carry a second, hand-written string guard
(``guard_sql``) that was never called on the request path and could be bypassed
(a leading ``/* comment */`` defeated ``startswith("select")``, and
``WITH x AS (SELECT 1) DELETE FROM t`` has no forbidden keyword). It was removed
so there is exactly one validator. The AST validator + table allowlist are
applied inside ``query_service.run_readonly`` (the only function that opens
DuckDB), and the connection itself is opened ``read_only=True``.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.audit import record_audit
from app.core.errors import Conflict, NotFound, TooManyRequests, ValidationProblem
from app.db.models import AgentRun, Dataset, Project
from app.db.session import get_session
from app.deps import AuthContext, client_ip, get_org_context, require_mutation, verify_csrf
from app.services import query_service
from dataloom_contracts._base import ActorKind, AgentRunStatus, AgentType, AgentOutputStatus
from dataloom_contracts.agents import SqlAnswer

router = APIRouter(tags=["agents"])


class AskRequest(BaseModel):
    """Body of ``POST /v1/agents/ask``."""

    project_id: uuid.UUID
    question: str = Field(min_length=3, max_length=2000)
    clarification_answer: str | None = Field(default=None, max_length=1000)


@router.post(
    "/agents/ask",
    dependencies=[Depends(verify_csrf)],
)
async def ask(
    payload: AskRequest,
    request: Request,
    ctx: AuthContext = Depends(require_mutation),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Ask a data question. Returns ``{agent_run, answer}``."""
    settings = get_settings()

    # Cost abuse is a DoS: every /agents/ask call may reach an LLM. A per-org
    # budget exists (FR-035) but a budget failure is detected *after* the call
    # chain starts; this is the cheap front door. Fail-closed is unnecessary
    # here (Redis down must not disable the product), matching SEC-22.
    from app.core.ratelimit import agent_rate_limit_ok

    if not await agent_rate_limit_ok(f"org:{ctx.org_id}:{ctx.user_id}"):
        raise TooManyRequests(
            "Batas permintaan agent tercapai. Coba lagi sebentar.", retry_after=30
        )

    project = (
        await session.execute(
            select(Project).where(
                Project.id == payload.project_id, Project.org_id == ctx.org_id
            )
        )
    ).scalar_one_or_none()
    if project is None:
        raise NotFound("Proyek tidak ditemukan.")

    # Concurrency + budget reservation (SEC-37). The runner's
    # ``create_agent_run`` enforces ``AGENT_MAX_CONCURRENT_PER_ORG``, but this
    # endpoint inserts the row itself, so without this check an org could open
    # unbounded concurrent LLM runs (each with its own token budget). The org's
    # per-run token limit is copied onto the row so the agent worker's
    # BudgetGuard has a real ceiling to enforce.
    active = (
        await session.execute(
            select(AgentRun.id).where(
                AgentRun.org_id == ctx.org_id,
                AgentRun.status.in_(
                    [
                        AgentRunStatus.PENDING.value,
                        AgentRunStatus.RUNNING.value,
                        AgentRunStatus.WAITING_APPROVAL.value,
                    ]
                ),
            )
        )
    ).all()
    if len(active) >= int(settings.AGENT_MAX_CONCURRENT_PER_ORG):
        raise Conflict(
            f"Batas {settings.AGENT_MAX_CONCURRENT_PER_ORG} run agent konkuren tercapai."
        )

    from app.db.models import Organization

    org = (
        await session.execute(
            select(Organization).where(Organization.id == ctx.org_id)
        )
    ).scalar_one_or_none()
    budget_tokens = int(org.per_run_token_limit) if org is not None else None

    agent_run = AgentRun(
        org_id=ctx.org_id,
        project_id=project.id,
        agent_type=AgentType.TEXT_TO_SQL.value,
        thread_id=f"text_to_sql:{uuid.uuid4()}",
        status=AgentRunStatus.RUNNING.value,
        budget_tokens=budget_tokens or settings.DEFAULT_PER_RUN_TOKEN_LIMIT,
        input_json={"question": payload.question[:2000]},
        created_by_user_id=ctx.user_id,
    )

    session.add(agent_run)
    await session.flush()

    await record_audit(
        session,
        org_id=ctx.org_id,
        action="agent.start",
        target_type="agent_run",
        actor_kind=ActorKind.USER.value,
        actor_id=ctx.user_id,
        actor_email=ctx.user.email,
        target_id=str(agent_run.id),
        metadata={"agent_type": AgentType.TEXT_TO_SQL.value},
        ip=client_ip(request),
    )

    # When the agent worker is disabled we return an explicit refusal. Inventing
    # SQL here would violate the "never guess" rule (PRD 10.1 rule 2).
    if not settings.AGENT_EXECUTION_ENABLED:
        agent_run.status = AgentRunStatus.FAILED.value
        agent_run.error_code = "execution_disabled"
        agent_run.error_message = "AGENT_EXECUTION_ENABLED=false"
        await session.flush()
        answer = SqlAnswer(
            status=AgentOutputStatus.REJECTED,
            question=payload.question,
            sql=None,
            columns=[],
            rows=[],
            row_count=0,
            truncated=False,
            explanation=(
                "Agent Text-to-SQL belum diaktifkan pada lingkungan ini. "
                "Tidak ada SQL yang dijalankan."
            ),
            assumptions=[],
            open_questions=[],
            rejection_code="EXECUTION_DISABLED",
        )
        return {
            "agent_run": {"id": str(agent_run.id), "thread_id": agent_run.thread_id},
            "answer": answer.model_dump(mode="json"),
        }

    # --- Enabled path ------------------------------------------------------
    from app.services.text_to_sql_service import run_text_to_sql

    answer = await run_text_to_sql(
        session,
        org_id=ctx.org_id,
        project_id=project.id,
        question=payload.question,
        clarification_answer=payload.clarification_answer,
        agent_run_id=agent_run.id,
    )
    agent_run.status = AgentRunStatus.SUCCEEDED.value
    await session.flush()
    return {
        "agent_run": {"id": str(agent_run.id), "thread_id": agent_run.thread_id},
        "answer": answer.model_dump(mode="json"),
    }


@router.get("/agents/ask/schema")
async def ask_schema(
    project_id: uuid.UUID,
    ctx: AuthContext = Depends(get_org_context),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Allowed tables/columns for the question box (read-only helper)."""
    project = (
        await session.execute(
            select(Project).where(Project.id == project_id, Project.org_id == ctx.org_id)
        )
    ).scalar_one_or_none()
    if project is None:
        raise NotFound("Proyek tidak ditemukan.")

    datasets = (
        await session.execute(
            select(Dataset).where(
                Dataset.org_id == ctx.org_id, Dataset.project_id == project.id
            )
        )
    ).scalars().all()
    return {
        "tables": [
            {
                "name": dataset.name,
                "layer": dataset.layer,
                "columns": (dataset.schema_json or {}).get("columns", []),
            }
            for dataset in datasets
        ]
    }

