"""Agent routes: start runs, read proposals, decide, apply (FR-030..037).

Approval is the only way a proposal becomes effective (FR-031):
``POST /v1/proposals/{id}/decision`` writes the decision + audit row, and
``POST /v1/agent-runs/{id}/apply`` refuses while a destructive proposal is
still pending.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import record_audit
from app.core.errors import Conflict, Forbidden, NotFound, TooManyRequests, ValidationProblem
from app.db.models import (
    AgentEvent,
    AgentProposal,
    AgentRun,
    Dataset,
    Pipeline,
    PipelineVersion,
    Project,
)
from app.db.session import get_session
from app.deps import (
    AuthContext,
    client_ip,
    get_org_context,
    get_project_or_404,
    require_mutation,
    require_role,
    verify_csrf,
)
from app.services import pipeline_service
from dataloom_contracts._base import (
    ActorKind,
    AgentRunStatus,
    AgentType,
    ProposalStatus,
    Role,
    RunStatus,
    ValidationStatus,
)
from dataloom_contracts.agents import (
    AgentEventOut,
    AgentProposalOut,
    AgentRunOut,
    ProfileReport,
    ProposalDecisionRequest,
    ProposalDecisionResponse,
)

router = APIRouter(tags=["agents"])


def _proposal_out(row: AgentProposal) -> AgentProposalOut:
    return AgentProposalOut(
        id=row.id,
        org_id=row.org_id,
        agent_run_id=row.agent_run_id,
        kind=row.kind,
        title=row.title,
        payload=row.payload_json or {},
        evidence=row.evidence_json,
        risk=row.risk,
        confidence=float(row.confidence) if row.confidence is not None else None,
        status=row.status,
        decided_by=row.decided_by,
        decided_at=row.decided_at,
        decision_note=row.decision_note,
        created_at=row.created_at,
    )


def _run_out(row: AgentRun) -> AgentRunOut:
    return AgentRunOut(
        id=row.id,
        org_id=row.org_id,
        project_id=row.project_id,
        agent_type=row.agent_type,
        thread_id=row.thread_id,
        status=row.status,
        budget_tokens=row.budget_tokens,
        tokens_used=row.tokens_used,
        cost_usd=float(row.cost_usd),
        current_step=row.current_step,
        started_at=row.started_at,
        finished_at=row.finished_at,
        error_code=row.error_code,
        error_message=row.error_message,
        created_at=row.created_at,
    )


async def _get_agent_run(
    agent_run_id: uuid.UUID, ctx: AuthContext, session: AsyncSession
) -> AgentRun:
    row = (
        await session.execute(
            select(AgentRun).where(
                AgentRun.id == agent_run_id, AgentRun.org_id == ctx.org_id
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise NotFound("Agent run tidak ditemukan.")
    return row


@router.post(
    "/agents/profile",
    response_model=dict,
    status_code=202,
    dependencies=[Depends(verify_csrf)],
)
async def start_profile(
    payload: dict,
    request: Request,
    ctx: AuthContext = Depends(require_mutation),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Create a Schema Profiler run (FR-030). Execution happens in the agent worker."""
    # LLM cost guard (SEC-40): one profiler run = several LLM calls. Reject
    # before any row is created so a flood cannot even fill `agent_runs`.
    from app.core.ratelimit import agent_rate_limit_ok

    if not await agent_rate_limit_ok(f"org:{ctx.org_id}:{ctx.user_id}"):
        raise TooManyRequests(
            "Batas permintaan agent tercapai. Coba lagi sebentar.", retry_after=30
        )

    project_id = payload.get("project_id")
    dataset_id = payload.get("dataset_id")
    if not project_id or not dataset_id:
        raise ValidationProblem("project_id dan dataset_id wajib diisi.")

    project = (

        await session.execute(
            select(Project).where(
                Project.id == uuid.UUID(str(project_id)), Project.org_id == ctx.org_id
            )
        )
    ).scalar_one_or_none()
    if project is None:
        raise NotFound("Proyek tidak ditemukan.")

    dataset = (
        await session.execute(
            select(Dataset).where(
                # org AND project: a dataset id from another project of the same
                # tenant must not be profilable through this project's URL/body
                # (FR-003 — "belong to the project", not merely to the org).
                Dataset.id == uuid.UUID(str(dataset_id)),
                Dataset.org_id == ctx.org_id,
                Dataset.project_id == project.id,
            )
        )
    ).scalar_one_or_none()
    if dataset is None:
        raise NotFound("Dataset tidak ditemukan.")

    agent_run = AgentRun(

        org_id=ctx.org_id,
        project_id=project.id,
        agent_type=AgentType.SCHEMA_PROFILER.value,
        thread_id=f"schema_profiler:{uuid.uuid4()}",
        status=AgentRunStatus.PENDING.value,
        target_dataset_id=dataset.id,
        input_json={"dataset_id": str(dataset.id)},
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
        metadata={"agent_type": agent_run.agent_type, "dataset_id": str(dataset.id)},
        ip=client_ip(request),
    )
    return {"agent_run": _run_out(agent_run).model_dump(mode="json")}


@router.post(
    "/agents/build-pipeline",
    response_model=dict,
    status_code=202,
    dependencies=[Depends(verify_csrf)],
)
async def start_pipeline_builder(
    payload: dict,
    request: Request,
    ctx: AuthContext = Depends(require_mutation),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Create a Pipeline Builder run (FR-032).

    The canvas "Susun draf" action posts here. Like the profiler, this only
    creates the ``agent_runs`` row; the LangGraph worker does the drafting, and
    the resulting DAG is still validated deterministically and needs approval
    before it can become a pipeline version.

    ``description`` is the field the UI sends; ``prompt`` is accepted as well so
    the request matches ``AgentRunCreateRequest`` naming without breaking the
    existing client.
    """
    from app.core.ratelimit import agent_rate_limit_ok

    if not await agent_rate_limit_ok(f"org:{ctx.org_id}:{ctx.user_id}"):
        raise TooManyRequests(
            "Batas permintaan agent tercapai. Coba lagi sebentar.", retry_after=30
        )

    project_id = payload.get("project_id")
    description = payload.get("description") or payload.get("prompt")
    if not project_id:
        raise ValidationProblem("project_id wajib diisi.")
    if not isinstance(description, str) or len(description.strip()) < 5:
        raise ValidationProblem("Deskripsi pipeline minimal 5 karakter.")

    project = (
        await session.execute(
            select(Project).where(
                Project.id == uuid.UUID(str(project_id)), Project.org_id == ctx.org_id
            )
        )
    ).scalar_one_or_none()
    if project is None:
        raise NotFound("Proyek tidak ditemukan.")

    dataset_id = payload.get("dataset_id")
    if dataset_id:
        dataset = (
            await session.execute(
                select(Dataset).where(
                    Dataset.id == uuid.UUID(str(dataset_id)),
                    Dataset.org_id == ctx.org_id,
                    Dataset.project_id == project.id,
                )
            )
        ).scalar_one_or_none()
        if dataset is None:
            raise NotFound("Dataset tidak ditemukan.")

    agent_run = AgentRun(
        org_id=ctx.org_id,
        project_id=project.id,
        agent_type=AgentType.PIPELINE_BUILDER.value,
        thread_id=f"pipeline_builder:{uuid.uuid4()}",
        status=AgentRunStatus.PENDING.value,
        target_dataset_id=uuid.UUID(str(dataset_id)) if dataset_id else None,
        input_json={"description": description.strip()[:4000]},
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
        metadata={"agent_type": agent_run.agent_type},
        ip=client_ip(request),
    )
    return {"agent_run": _run_out(agent_run).model_dump(mode="json")}


@router.get("/agent-runs/{agent_run_id}", response_model=AgentRunOut)
async def get_agent_run(
    agent_run_id: uuid.UUID,
    ctx: AuthContext = Depends(get_org_context),
    session: AsyncSession = Depends(get_session),
) -> AgentRunOut:
    return _run_out(await _get_agent_run(agent_run_id, ctx, session))


@router.get("/agent-runs/{agent_run_id}/proposals", response_model=list[AgentProposalOut])
async def list_proposals(
    agent_run_id: uuid.UUID,
    ctx: AuthContext = Depends(get_org_context),
    session: AsyncSession = Depends(get_session),
) -> list[AgentProposalOut]:
    await _get_agent_run(agent_run_id, ctx, session)
    rows = (
        await session.execute(
            select(AgentProposal)
            .where(
                AgentProposal.agent_run_id == agent_run_id,
                AgentProposal.org_id == ctx.org_id,
            )
            .order_by(AgentProposal.created_at.asc())
        )
    ).scalars().all()
    return [_proposal_out(row) for row in rows]


@router.get("/agent-runs/{agent_run_id}/events", response_model=list[AgentEventOut])
async def list_events(
    agent_run_id: uuid.UUID,
    ctx: AuthContext = Depends(get_org_context),
    session: AsyncSession = Depends(get_session),
) -> list[AgentEventOut]:
    await _get_agent_run(agent_run_id, ctx, session)
    rows = (
        await session.execute(
            select(AgentEvent)
            .where(AgentEvent.agent_run_id == agent_run_id, AgentEvent.org_id == ctx.org_id)
            .order_by(AgentEvent.at.asc())
        )
    ).scalars().all()
    return [
        AgentEventOut(
            id=row.id,
            agent_run_id=row.agent_run_id,
            step=row.step,
            node_name=row.node_name,
            status=row.status,
            payload=row.payload_json,
            at=row.at,
        )
        for row in rows
    ]


@router.get("/agent-runs/{agent_run_id}/profile", response_model=ProfileReport)
async def get_profile(
    agent_run_id: uuid.UUID,
    ctx: AuthContext = Depends(get_org_context),
    session: AsyncSession = Depends(get_session),
) -> ProfileReport:
    """Reassemble a ``ProfileReport`` from persisted proposals (no LLM call)."""
    run = await _get_agent_run(agent_run_id, ctx, session)
    proposals = (
        await session.execute(
            select(AgentProposal).where(
                AgentProposal.agent_run_id == agent_run_id,
                AgentProposal.org_id == ctx.org_id,
            )
        )
    ).scalars().all()

    from dataloom_contracts._base import AgentOutputStatus
    from dataloom_contracts.agents import CleaningProposal

    dataset_id = run.target_dataset_id or uuid.UUID(int=0)
    return ProfileReport(
        status=AgentOutputStatus.OK if proposals else AgentOutputStatus.INSUFFICIENT_EVIDENCE,
        dataset_id=dataset_id,
        sampled_rows=int((run.input_json or {}).get("sampled_rows", 0)),
        total_rows=None,
        columns=[],
        anomalies=[],
        cleaning_proposals=[
            CleaningProposal.model_validate(row.payload_json) for row in proposals
        ],
        open_questions=[] if proposals else ["Dataset belum diprofilkan atau bukti tidak cukup."],
        summary=None,
    )


@router.post(
    "/proposals/{proposal_id}/decision",
    response_model=ProposalDecisionResponse,
    dependencies=[Depends(verify_csrf)],
)
async def decide_proposal(
    proposal_id: uuid.UUID,
    payload: ProposalDecisionRequest,
    request: Request,
    ctx: AuthContext = Depends(require_mutation),
    session: AsyncSession = Depends(get_session),
) -> ProposalDecisionResponse:
    """Approve/reject exactly one proposal (FR-031). Audited."""
    row = (
        await session.execute(
            select(AgentProposal).where(
                AgentProposal.id == proposal_id, AgentProposal.org_id == ctx.org_id
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise NotFound("Usulan tidak ditemukan.")
    if row.status != ProposalStatus.PENDING.value:
        raise Conflict(f"Usulan sudah diputuskan ({row.status}).")

    # Contract (dataloom_contracts.agents.ProposalDecisionRequest) uses
    # ``approve`` / ``reject``; the DB stores ``approved`` / ``rejected``.
    # Mapping happens here, in exactly one place, from the validated contract
    # value — do not re-derive it from a differently-spelled string (SEC-42).
    new_status = (
        ProposalStatus.APPROVED.value
        if payload.decision == "approve"
        else ProposalStatus.REJECTED.value
    )

    row.status = new_status
    row.decided_by = ctx.user_id
    row.decided_at = datetime.now(tz=timezone.utc)
    row.decision_note = payload.note
    await session.flush()

    audit = await record_audit(
        session,
        org_id=ctx.org_id,
        action="proposal.decision",
        target_type="agent_proposal",
        actor_kind=ActorKind.USER.value,
        actor_id=ctx.user_id,
        actor_email=ctx.user.email,
        target_id=str(row.id),
        metadata={"decision": payload.decision, "risk": row.risk},
        ip=client_ip(request),
    )

    return ProposalDecisionResponse(
        proposal=_proposal_out(row),
        applied=False,
        new_pipeline_version_id=row.applied_pipeline_version_id,
        audit_log_id=getattr(audit, "id", None),
    )


@router.post(
    "/agent-runs/{agent_run_id}/apply-fix",
    dependencies=[Depends(verify_csrf)],
)
async def apply_healing_fix_endpoint(
    agent_run_id: uuid.UUID,
    payload: dict | None,
    request: Request,
    ctx: AuthContext = Depends(require_role(Role.ADMIN)),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Execute an approved healing fix: new version + requeue (owner/admin).

    Owner/admin only: the fix creates a new pipeline version and enqueues a
    run, both billable side effects. Tenant-scoped via ``ctx.org_id``.
    """
    from app.services import healing_service

    if ctx.role not in ("owner", "admin"):
        raise Forbidden("Hanya owner/admin yang dapat menerapkan healing fix.")
    body = payload or {}
    result = await healing_service.apply_approved_healing_fix(
        session,
        org_id=ctx.org_id,
        agent_run_id=agent_run_id,
        user_id=ctx.user_id,
        actor_email=ctx.user.email,
        requeue=bool(body.get("requeue", True)),
        change_note=body.get("change_note"),
        ip=client_ip(request),
    )
    return {
        "pipeline_id": str(result.pipeline_id),
        "pipeline_version_id": str(result.pipeline_version_id),
        "version": result.version,
        "applied_proposals": result.applied_proposals,
        "requeued_run_id": str(result.requeued_run_id) if result.requeued_run_id else None,
    }


@router.post(
    "/agent-runs/{agent_run_id}/apply",
    dependencies=[Depends(verify_csrf)],
)
async def apply_approved(
    agent_run_id: uuid.UUID,
    payload: dict,
    request: Request,
    ctx: AuthContext = Depends(require_mutation),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Create a NEW pipeline version from approved cleaning rules.

    Refuses when a destructive proposal is still pending. The old version is
    never modified (FR-024/FR-034).
    """
    run = await _get_agent_run(agent_run_id, ctx, session)
    pipeline_id = payload.get("pipeline_id")
    if not pipeline_id:
        raise ValidationProblem("pipeline_id wajib diisi.")

    pipeline = await pipeline_service.get_pipeline_scoped(
        session, org_id=ctx.org_id, pipeline_id=uuid.UUID(str(pipeline_id))
    )

    rows = (
        await session.execute(
            select(AgentProposal).where(
                AgentProposal.agent_run_id == agent_run_id,
                AgentProposal.org_id == ctx.org_id,
            )
        )
    ).scalars().all()

    pending_destructive = [
        row
        for row in rows
        if row.risk == "destructive" and row.status == ProposalStatus.PENDING.value
    ]
    if pending_destructive:
        raise Conflict(
            "Ada usulan destruktif yang belum diputuskan. Putuskan setiap usulan "
            "destruktif sebelum menerapkan."
        )

    approved = [row for row in rows if row.status == ProposalStatus.APPROVED.value]
    if not approved:
        raise ValidationProblem("Belum ada usulan yang disetujui.")

    # Clone the current DAG and append a clean node with the approved operations.
    if pipeline.current_version_id is None:
        raise Conflict("Pipeline belum memiliki versi. Buat versi awal terlebih dahulu.")
    base_version = await pipeline_service.get_version(
        session, org_id=ctx.org_id, version_id=pipeline.current_version_id
    )
    dag_payload = dict(base_version.dag_json)
    nodes = list(dag_payload.get("nodes", []))
    edges = list(dag_payload.get("edges", []))

    clean_node_id = f"n_clean_agent_{len(nodes) + 1}"
    operations = [
        {
            "op": row.payload_json.get("op"),
            "column": row.payload_json.get("column"),
            "params": row.payload_json.get("params", {}),
            "risk": row.risk,
        }
        for row in approved
    ]
    nodes.append(
        {
            "id": clean_node_id,
            "type": "clean",
            "label": "Pembersihan (agent)",
            "params": {"kind": "clean", "operations": operations},
        }
    )
    # Insert the clean node before the first sink node so the sink still receives
    # a transformed frame; if no sink exists, append after the last node.
    sink_ids = [node["id"] for node in nodes if node.get("type") == "sink"]
    if sink_ids:
        first_sink = sink_ids[0]
        incoming = [edge for edge in edges if edge.get("to") == first_sink]
        edges = [edge for edge in edges if edge.get("to") != first_sink]
        for edge in incoming:
            edges.append({"from": edge.get("from"), "to": clean_node_id})
        edges.append({"from": clean_node_id, "to": first_sink})
    else:
        last_id = nodes[-2]["id"] if len(nodes) >= 2 else nodes[0]["id"]
        edges.append({"from": last_id, "to": clean_node_id})

    dag_payload["nodes"] = nodes
    dag_payload["edges"] = edges

    version = await pipeline_service.replace_dag(
        session,
        pipeline=pipeline,
        dag_payload=dag_payload,
        user_id=ctx.user_id,
        change_note=f"Aturan pembersihan dari agent run {agent_run_id}",
    )

    for row in approved:
        row.applied_pipeline_version_id = version.id
    # State-machine integrity (SEC-01/SEC-43): do NOT overwrite a terminal status.
    # A run that ended in ``insufficient_evidence``/``failed`` must not be relabelled
    # "succeeded" just because the user applied rules from it. Only the
    # waiting-for-approval (and, defensively, pending/running) states advance.
    if run.status in (
        AgentRunStatus.WAITING_APPROVAL.value,
        AgentRunStatus.PENDING.value,
        AgentRunStatus.RUNNING.value,
    ):
        run.status = AgentRunStatus.SUCCEEDED.value
        run.finished_at = datetime.now(tz=timezone.utc)
    await session.flush()


    await record_audit(
        session,
        org_id=ctx.org_id,
        action="proposal.decision",
        target_type="pipeline_version",
        actor_kind=ActorKind.USER.value,
        actor_id=ctx.user_id,
        actor_email=ctx.user.email,
        target_id=str(version.id),
        metadata={"agent_run_id": str(agent_run_id), "applied": len(approved)},
        ip=client_ip(request),
    )

    return {
        "pipeline_id": str(pipeline.id),
        "pipeline_version_id": str(version.id),
        "version": version.version,
        "applied_proposals": len(approved),
    }


