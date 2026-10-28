"""Run Monitor API: run detail, node list, gate checklist, live SSE (FR-040).

SSE design: the worker writes to Postgres, the API polls the DB every
``SSE_POLL_SECONDS`` and emits only *new* events (by monotonic ``seq`` computed
from row timestamps). This keeps a single source of truth (the DB) instead of
a second, in-memory pub/sub channel that could desync after a restart.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import record_audit
from app.core.errors import Forbidden, NotFound
from app.core.sse import format_event, sse_response
from app.db.models import Pipeline, Project, Run, RunNode
from app.db.session import get_session, get_session_factory
from app.deps import AuthContext, client_ip, get_org_context, require_mutation, verify_csrf
from app.services.run_service import cancel_run
from dataloom_contracts._base import GateDecision, RunEventType
from dataloom_contracts.projects import ProjectSettings
from dataloom_contracts.runs import (
    CancelRunResponse,
    GateChecklist,
    RunDetail,
    RunNodeOut,
    RunOut,
)

router = APIRouter(tags=["runs"])

SSE_POLL_SECONDS = 1.0


def _ratio(rows_out: int, rows_quarantined: int) -> float | None:
    total = rows_out + rows_quarantined
    if total <= 0:
        return None
    return round(rows_quarantined / total, 6)


def _run_out(run: Run) -> RunOut:
    return RunOut(
        id=run.id,
        org_id=run.org_id,
        project_id=run.project_id,
        pipeline_id=run.pipeline_id,
        pipeline_version_id=run.pipeline_version_id,
        version=run.version,
        trigger=run.trigger,
        status=run.status,
        current_node_id=run.current_node_id,
        queued_at=run.queued_at,
        started_at=run.started_at,
        finished_at=run.finished_at,
        duration_ms=run.duration_ms,
        rows_in=run.rows_in,
        rows_out=run.rows_out,
        rows_quarantined=run.rows_quarantined,
        quarantine_ratio=_ratio(run.rows_out, run.rows_quarantined),
        retry_total=run.retry_total,
        error_code=run.error_code,
        error_message=run.error_message,
        created_at=run.created_at,
    )


def _node_out(node: RunNode) -> RunNodeOut:
    return RunNodeOut(
        id=node.id,
        run_id=node.run_id,
        node_id=node.node_id,
        node_type=node.node_type,
        status=node.status,
        retry_count=node.retry_count,
        started_at=node.started_at,
        finished_at=node.finished_at,
        duration_ms=node.duration_ms,
        rows_in=node.rows_in,
        rows_out=node.rows_out,
        rows_quarantined=node.rows_quarantined,
        error_code=node.error_code,
        error_message=node.error_message,
        error=node.error_json,
    )


async def _get_run_scoped(
    session: AsyncSession, *, org_id: uuid.UUID, run_id: uuid.UUID
) -> Run:
    run = (
        await session.execute(
            select(Run).where(Run.id == run_id, Run.org_id == org_id)
        )
    ).scalar_one_or_none()
    if run is None:
        raise NotFound("Run tidak ditemukan.")
    return run


async def _gate_for_run(
    session: AsyncSession, run: Run, project: Project
) -> GateChecklist:
    """Recompute the A0 gate from persisted facts (never from an LLM)."""
    settings = ProjectSettings.model_validate(project.settings_json or {})
    nodes = (
        await session.execute(select(RunNode).where(RunNode.run_id == run.id))
    ).scalars().all()

    failed_nodes = [n for n in nodes if n.status == "failed"]
    schema_valid = len(failed_nodes) == 0
    ratio = _ratio(run.rows_out, run.rows_quarantined) or 0.0
    reconciliation_ok: bool | None = None
    # The worker stores reconciliation evidence on the failing/aggregate node.
    for node in nodes:
        if node.error_json and "reconciliation" in str(node.error_json):
            reconciliation_ok = bool(node.error_json.get("reconciliation", {}).get("passed"))
    if run.status == "succeeded":
        reconciliation_ok = True if reconciliation_ok is None else reconciliation_ok

    return GateChecklist.build(
        run_id=run.id,
        evaluated_at=datetime.now(tz=timezone.utc),
        schema_valid=schema_valid,
        quarantine_ratio=ratio,
        max_quarantine_ratio=settings.max_quarantine_ratio,
        reconciliation_ok=reconciliation_ok,
        pending_required_proposals=0,
    )


@router.get("/projects/{project_id}/runs", response_model=list[RunOut])
async def list_runs(
    project_id: uuid.UUID,
    ctx: AuthContext = Depends(get_org_context),
    session: AsyncSession = Depends(get_session),
    limit: int = Query(default=50, ge=1, le=200),
    status: str | None = Query(default=None),
) -> list[RunOut]:
    project = (
        await session.execute(
            select(Project).where(Project.id == project_id, Project.org_id == ctx.org_id)
        )
    ).scalar_one_or_none()
    if project is None:
        raise NotFound("Proyek tidak ditemukan.")

    stmt = (
        select(Run)
        .where(Run.project_id == project.id, Run.org_id == ctx.org_id)
        .order_by(Run.created_at.desc())
        .limit(limit)
    )
    if status:
        stmt = stmt.where(Run.status == status)
    rows = (await session.execute(stmt)).scalars().all()
    return [_run_out(r) for r in rows]


@router.get("/runs/{run_id}", response_model=RunDetail)
async def get_run(
    run_id: uuid.UUID,
    ctx: AuthContext = Depends(get_org_context),
    session: AsyncSession = Depends(get_session),
) -> RunDetail:
    run = await _get_run_scoped(session, org_id=ctx.org_id, run_id=run_id)
    nodes = (
        await session.execute(
            select(RunNode)
            .where(RunNode.run_id == run.id, RunNode.org_id == ctx.org_id)
            .order_by(RunNode.created_at.asc())
        )
    ).scalars().all()

    project = (
        await session.execute(select(Project).where(Project.id == run.project_id))
    ).scalar_one_or_none()

    gate = await _gate_for_run(session, run, project) if project else None
    base = _run_out(run)
    return RunDetail(**base.model_dump(), nodes=[_node_out(n) for n in nodes], gate=gate)


@router.delete(
    "/runs/{run_id}",
    response_model=CancelRunResponse,
    dependencies=[Depends(verify_csrf)],
)
async def cancel(
    run_id: uuid.UUID,
    request: Request,
    ctx: AuthContext = Depends(require_mutation),
    session: AsyncSession = Depends(get_session),
) -> CancelRunResponse:
    run = await cancel_run(session, org_id=ctx.org_id, run_id=run_id)
    await record_audit(
        session,
        org_id=ctx.org_id,
        action="run.cancel",
        target_type="run",
        actor_id=ctx.user_id,
        actor_email=ctx.user.email,
        target_id=str(run.id),
        ip=client_ip(request),
    )
    return CancelRunResponse(
        run_id=run.id,
        status=run.status,
        cancelled_at=datetime.now(tz=timezone.utc),
    )


@router.get("/runs/{run_id}/events")
async def run_events(
    run_id: uuid.UUID,
    request: Request,
    ctx: AuthContext = Depends(get_org_context),
    session: AsyncSession = Depends(get_session),
) -> object:
    """SSE stream of run events. Reconnect-safe: the client may send
    ``Last-Event-ID``; we replay from the current DB state either way."""
    run = await _get_run_scoped(session, org_id=ctx.org_id, run_id=run_id)
    org_id = ctx.org_id

    async def generator() -> AsyncIterator[str]:
        factory = get_session_factory()
        seq = 0
        last_run_status = run.status
        seen_nodes: dict[str, str] = {}
        terminal = {"succeeded", "failed", "cancelled", "needs_attention"}

        while True:
            if await request.is_disconnected():
                return
            async with factory() as poll_session:
                current = (
                    await poll_session.execute(
                        select(Run).where(Run.id == run.id, Run.org_id == org_id)
                    )
                ).scalar_one_or_none()
                if current is None:
                    return

                if current.status != last_run_status:
                    seq += 1
                    yield format_event(
                        RunEventType.RUN_STATUS.value,
                        {
                            "event": RunEventType.RUN_STATUS.value,
                            "run_id": str(run.id),
                            "seq": seq,
                            "emitted_at": datetime.now(tz=timezone.utc).isoformat(),
                            "status": current.status,
                            "previous_status": last_run_status,
                            "rows_in": current.rows_in,
                            "rows_out": current.rows_out,
                            "rows_quarantined": current.rows_quarantined,
                            "error_code": current.error_code,
                            "error_message": current.error_message,
                        },
                        event_id=str(seq),
                    )
                    last_run_status = current.status

                nodes = (
                    await poll_session.execute(
                        select(RunNode)
                        .where(RunNode.run_id == run.id)
                        .order_by(RunNode.created_at.asc())
                    )
                ).scalars().all()
                for node in nodes:
                    previous = seen_nodes.get(node.node_id)
                    if previous != node.status:
                        seen_nodes[node.node_id] = node.status
                        seq += 1
                        yield format_event(
                            RunEventType.NODE_STATUS.value,
                            {
                                "event": RunEventType.NODE_STATUS.value,
                                "run_id": str(run.id),
                                "seq": seq,
                                "emitted_at": datetime.now(tz=timezone.utc).isoformat(),
                                "node_id": node.node_id,
                                "node_type": node.node_type,
                                "status": node.status,
                                "retry_count": node.retry_count,
                                "duration_ms": node.duration_ms,
                                "rows_in": node.rows_in,
                                "rows_out": node.rows_out,
                                "rows_quarantined": node.rows_quarantined,
                                "error_code": node.error_code,
                                "error_message": node.error_message,
                            },
                            event_id=str(seq),
                        )

                if current.status in terminal:
                    seq += 1
                    yield format_event(
                        "done",
                        {
                            "run_id": str(run.id),
                            "status": current.status,
                            "seq": seq,
                            "emitted_at": datetime.now(tz=timezone.utc).isoformat(),
                        },
                        event_id=str(seq),
                    )
                    return

            await asyncio.sleep(SSE_POLL_SECONDS)

    return sse_response(generator())
