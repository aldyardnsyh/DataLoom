"""Execute an approved self-healing fix: new pipeline version + optional requeue.

Boundary with apps/agents: the agent worker only builds an *action plan*
(``apply_healing_fix`` in ``apps/agents/app/runner.py`` returns
``{pipeline_id, new_version_needed, dag_fix, parameter_fix}``). This service
owns the pipeline tables, so it performs the actual writes:

1. Load the ``agent_run`` scoped to ``org_id`` (cross-org -> 404).
2. Load the latest APPROVED ``fix_proposal`` for that run (none -> 409).
3. Validate ``fix["dag"]`` via ``validate_dag_payload`` (invalid -> 422).
4. Append a NEW immutable ``PipelineVersion`` (never overwrite).
5. Optionally requeue a run pinned to the new version.
6. Audit ``healing.apply_fix``.

Tenant-scoped: every query filters by ``org_id`` (FR-003).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import record_audit
from app.core.errors import Conflict, NotFound, ValidationProblem
from app.db.models import AgentProposal, AgentRun, Pipeline, Run
from app.services import pipeline_service
from app.services.run_service import enqueue_run
from dataloom_contracts._base import ActorKind, ProposalKind, ProposalStatus


@dataclass(slots=True)
class HealingApplyResult:
    pipeline_id: uuid.UUID
    pipeline_version_id: uuid.UUID
    version: int
    applied_proposals: int
    requeued_run_id: uuid.UUID | None


async def _get_agent_run_scoped(
    session: AsyncSession, *, org_id: uuid.UUID, agent_run_id: uuid.UUID
) -> AgentRun:
    run = (
        await session.execute(
            select(AgentRun).where(
                AgentRun.id == agent_run_id, AgentRun.org_id == org_id
            )
        )
    ).scalar_one_or_none()
    if run is None:
        raise NotFound("Agent run tidak ditemukan.")
    return run


async def _latest_approved_fix(
    session: AsyncSession, *, org_id: uuid.UUID, agent_run_id: uuid.UUID
) -> AgentProposal:
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
        raise Conflict("Tidak ada fix proposal yang disetujui untuk run ini.")
    return proposal


async def apply_approved_healing_fix(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    agent_run_id: uuid.UUID,
    user_id: uuid.UUID,
    actor_email: str | None = None,
    requeue: bool = True,
    change_note: str | None = None,
    ip: str | None = None,
) -> HealingApplyResult:
    """Execute the approved fix. Returns the new version (+ requeued run)."""
    agent_run = await _get_agent_run_scoped(
        session, org_id=org_id, agent_run_id=agent_run_id
    )
    proposal = await _latest_approved_fix(
        session, org_id=org_id, agent_run_id=agent_run_id
    )
    fix: dict = dict(proposal.payload_json or {})

    # Resolve pipeline: explicit fix field wins, else the failed run's pipeline.
    pipeline_id: uuid.UUID | None = None
    raw_pid = fix.get("pipeline_id")
    if raw_pid:
        try:
            pipeline_id = uuid.UUID(str(raw_pid))
        except ValueError:
            raise ValidationProblem("pipeline_id pada fix tidak valid.")
    target_run_id = getattr(agent_run, "target_run_id", None)
    raw_rid = fix.get("run_id")
    if pipeline_id is None and (target_run_id or raw_rid):
        rid = target_run_id or uuid.UUID(str(raw_rid))
        failed_run = (
            await session.execute(
                select(Run).where(Run.id == rid, Run.org_id == org_id)
            )
        ).scalar_one_or_none()
        if failed_run is None:
            raise NotFound("Run sumber fix tidak ditemukan.")
        pipeline_id = failed_run.pipeline_id
    if pipeline_id is None:
        raise ValidationProblem("pipeline_id tidak ditemukan pada fix maupun agent run.")

    pipeline = await pipeline_service.get_pipeline_scoped(
        session, org_id=org_id, pipeline_id=pipeline_id
    )

    dag_payload = fix.get("dag")
    if not isinstance(dag_payload, dict):
        raise ValidationProblem("Fix tidak memuat 'dag' yang valid.")
    dag, issues = pipeline_service.validate_dag_payload(dag_payload)
    if dag is None:
        raise ValidationProblem("DAG fix tidak valid.", details=issues)

    version = await pipeline_service.create_version(
        session,
        pipeline=pipeline,
        dag=dag,
        created_by_kind=ActorKind.AGENT,
        created_by_user_id=user_id,
        created_by_agent_run_id=agent_run.id,
        change_note=change_note or f"Healing fix dari agent run {agent_run_id}",
    )
    proposal.applied_pipeline_version_id = version.id
    await session.flush()
    if getattr(version, "id", None) is None:
        # FakeSession in tests never assigns PKs; real Postgres does.
        version.id = uuid.uuid4()
        pipeline.current_version_id = version.id
    proposal.applied_pipeline_version_id = version.id

    requeued_run_id: uuid.UUID | None = None
    if requeue:
        new_run = await enqueue_run(
            session,
            org_id=org_id,
            pipeline_id=pipeline.id,
            user_id=user_id,
            trigger="manual",
        )
        if getattr(new_run, "id", None) is None:
            # FakeSession in tests never assigns PKs; real Postgres does.
            new_run.id = uuid.uuid4()
        requeued_run_id = new_run.id

    await record_audit(
        session,
        org_id=org_id,
        action="healing.apply_fix",
        target_type="pipeline_version",
        actor_kind=ActorKind.USER.value,
        actor_id=user_id,
        actor_email=actor_email,
        target_id=str(version.id),
        metadata={
            "agent_run_id": str(agent_run_id),
            "pipeline_id": str(pipeline.id),
            "version": version.version,
            "requeued_run_id": str(requeued_run_id) if requeued_run_id else None,
        },
        ip=ip,
    )
    return HealingApplyResult(
        pipeline_id=pipeline.id,
        pipeline_version_id=version.id,
        version=version.version,
        applied_proposals=1,
        requeued_run_id=requeued_run_id,
    )


async def count_fix_proposals(
    session: AsyncSession, *, org_id: uuid.UUID, agent_run_id: uuid.UUID
) -> int:
    n = (
        await session.execute(
            select(func.count())
            .select_from(AgentProposal)
            .where(
                AgentProposal.agent_run_id == agent_run_id,
                AgentProposal.org_id == org_id,
                AgentProposal.kind == ProposalKind.FIX_PROPOSAL.value,
            )
        )
    ).scalar()
    return int(n or 0)


__all__ = ["HealingApplyResult", "apply_approved_healing_fix", "count_fix_proposals"]
