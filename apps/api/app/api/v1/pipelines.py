"""Pipeline routes (FR-020, FR-024, FR-027, FR-028)."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import record_audit
from app.db.models import Pipeline, PipelineVersion, Project
from app.db.session import get_session
from app.deps import (
    AuthContext,
    client_ip,
    get_org_context,
    get_project_or_404,
    require_mutation,
    verify_csrf,
)
from app.services import pipeline_service
from app.services.run_service import enqueue_run
from dataloom_contracts._base import ActorKind
from dataloom_contracts.pipelines import (
    PipelineCreate,
    PipelineDAG,
    PipelineOut,
    PipelineUpdate,
    PipelineValidateRequest,
    PipelineValidateResponse,
    PipelineVersionOut,
)
from dataloom_contracts.runs import RunCreateRequest, RunCreateResponse, RunOut

router = APIRouter(tags=["pipelines"])


def _pipeline_out(pipeline: Pipeline, current_version: int | None) -> PipelineOut:
    return PipelineOut(
        id=pipeline.id,
        org_id=pipeline.org_id,
        project_id=pipeline.project_id,
        name=pipeline.name,
        description=pipeline.description,
        current_version_id=pipeline.current_version_id,
        current_version=current_version,
        schedule_cron=pipeline.schedule_cron,
        created_at=pipeline.created_at,
        updated_at=pipeline.updated_at,
    )


def _version_out(version: PipelineVersion) -> PipelineVersionOut:
    return PipelineVersionOut(
        id=version.id,
        org_id=version.org_id,
        pipeline_id=version.pipeline_id,
        version=version.version,
        dag=PipelineDAG.model_validate(version.dag_json),
        validation_status=version.validation_status,
        validation_errors=version.validation_errors or [],
        created_by_kind=version.created_by_kind,
        created_by_user_id=version.created_by_user_id,
        created_by_agent_run_id=version.created_by_agent_run_id,
        parent_version_id=version.parent_version_id,
        change_note=version.change_note,
        created_at=version.created_at,
    )


async def _current_version_number(
    session: AsyncSession, pipeline: Pipeline
) -> int | None:
    if pipeline.current_version_id is None:
        return None
    return (
        await session.execute(
            select(PipelineVersion.version).where(
                PipelineVersion.id == pipeline.current_version_id
            )
        )
    ).scalar_one_or_none()


@router.get("/projects/{project_id}/pipelines", response_model=list[PipelineOut])
async def list_pipelines(
    project: Project = Depends(get_project_or_404),
    session: AsyncSession = Depends(get_session),
) -> list[PipelineOut]:
    rows = (
        await session.execute(
            select(Pipeline)
            .where(Pipeline.project_id == project.id, Pipeline.org_id == project.org_id)
            .order_by(Pipeline.updated_at.desc())
        )
    ).scalars().all()

    version_map: dict[uuid.UUID, int] = {}
    if rows:
        versions = (
            await session.execute(
                select(PipelineVersion.id, PipelineVersion.version).where(
                    PipelineVersion.org_id == project.org_id
                )
            )
        ).all()
        version_map = {vid: number for vid, number in versions}

    return [
        _pipeline_out(p, version_map.get(p.current_version_id) if p.current_version_id else None)
        for p in rows
    ]


@router.post(
    "/projects/{project_id}/pipelines",
    response_model=PipelineOut,
    status_code=201,
    dependencies=[Depends(verify_csrf)],
)
async def create_pipeline(
    payload: PipelineCreate,
    request: Request,
    project: Project = Depends(get_project_or_404),
    ctx: AuthContext = Depends(require_mutation),
    session: AsyncSession = Depends(get_session),
) -> PipelineOut:
    pipeline = await pipeline_service.create_pipeline(
        session,
        org_id=project.org_id,
        project_id=project.id,
        name=payload.name,
        description=payload.description,
        dag_payload=payload.dag.model_dump(mode="json", by_alias=True),
        user_id=ctx.user_id,
    )
    await record_audit(
        session,
        org_id=project.org_id,
        action="pipeline.create",
        target_type="pipeline",
        actor_id=ctx.user_id,
        actor_email=ctx.user.email,
        target_id=str(pipeline.id),
        ip=client_ip(request),
    )
    version = await _current_version_number(session, pipeline)
    return _pipeline_out(pipeline, version)


@router.get("/pipelines/{pipeline_id}", response_model=PipelineOut)
async def get_pipeline(
    pipeline_id: uuid.UUID,
    ctx: AuthContext = Depends(get_org_context),
    session: AsyncSession = Depends(get_session),
) -> PipelineOut:
    pipeline = await pipeline_service.get_pipeline_scoped(
        session, org_id=ctx.org_id, pipeline_id=pipeline_id
    )
    return _pipeline_out(pipeline, await _current_version_number(session, pipeline))


@router.patch(
    "/pipelines/{pipeline_id}",
    response_model=PipelineVersionOut | None,
    dependencies=[Depends(verify_csrf)],
)
async def update_pipeline(
    pipeline_id: uuid.UUID,
    payload: PipelineUpdate,
    request: Request,
    ctx: AuthContext = Depends(require_mutation),
    session: AsyncSession = Depends(get_session),
) -> PipelineVersionOut | None:
    """Editing always appends a new version; the old one is untouched (FR-024)."""
    pipeline = await pipeline_service.get_pipeline_scoped(
        session, org_id=ctx.org_id, pipeline_id=pipeline_id
    )
    if payload.name is not None:
        pipeline.name = payload.name.strip()
    if payload.description is not None:
        pipeline.description = payload.description

    version: PipelineVersion | None = None
    if payload.dag is not None:
        version = await pipeline_service.create_version(
            session,
            pipeline=pipeline,
            dag=payload.dag,
            created_by_kind=ActorKind.USER,
            created_by_user_id=ctx.user_id,
            change_note=payload.change_note,
        )
    await session.flush()
    await record_audit(
        session,
        org_id=ctx.org_id,
        action="pipeline.update" if version is None else "pipeline.version.create",
        target_type="pipeline",
        actor_id=ctx.user_id,
        actor_email=ctx.user.email,
        target_id=str(pipeline.id),
        metadata={"new_version": version.version if version else None},
        ip=client_ip(request),
    )
    return _version_out(version) if version is not None else None


@router.get("/pipelines/{pipeline_id}/versions", response_model=list[PipelineVersionOut])
async def list_versions(
    pipeline_id: uuid.UUID,
    ctx: AuthContext = Depends(get_org_context),
    session: AsyncSession = Depends(get_session),
) -> list[PipelineVersionOut]:
    await pipeline_service.get_pipeline_scoped(
        session, org_id=ctx.org_id, pipeline_id=pipeline_id
    )
    versions = await pipeline_service.list_versions(
        session, org_id=ctx.org_id, pipeline_id=pipeline_id
    )
    return [_version_out(v) for v in versions]


@router.get("/pipeline-versions/{version_id}", response_model=PipelineVersionOut)
async def get_version(
    version_id: uuid.UUID,
    ctx: AuthContext = Depends(get_org_context),
    session: AsyncSession = Depends(get_session),
) -> PipelineVersionOut:
    version = await pipeline_service.get_version(
        session, org_id=ctx.org_id, version_id=version_id
    )
    return _version_out(version)


@router.post("/pipelines/validate", response_model=PipelineValidateResponse)
async def validate_pipeline(
    payload: PipelineValidateRequest,
    ctx: AuthContext = Depends(get_org_context),
) -> PipelineValidateResponse:
    """Deterministic validation only. Same validator the UI and agents use."""
    try:
        dag = PipelineDAG.model_validate(payload.dag.model_dump(mode="json", by_alias=True))
    except ValidationError as exc:
        return PipelineValidateResponse(
            valid=False, issues=pipeline_service.format_dag_errors(exc)
        )
    return PipelineValidateResponse(
        valid=True, issues=[], topological_order=dag.topological_order()
    )


@router.post(
    "/pipelines/{pipeline_id}/runs",
    response_model=RunCreateResponse,
    status_code=202,
    dependencies=[Depends(verify_csrf)],
)
async def create_run(
    pipeline_id: uuid.UUID,
    payload: RunCreateRequest,
    request: Request,
    ctx: AuthContext = Depends(require_mutation),
    session: AsyncSession = Depends(get_session),
) -> RunCreateResponse:
    """Enqueue a run pinned to a specific immutable version (FR-024, FR-025)."""
    pipeline = await pipeline_service.get_pipeline_scoped(
        session, org_id=ctx.org_id, pipeline_id=pipeline_id
    )
    run = await enqueue_run(
        session,
        org_id=ctx.org_id,
        pipeline_id=pipeline.id,
        user_id=ctx.user_id,
        trigger=payload.trigger.value,
        idempotency_key=payload.idempotency_key,
    )
    await record_audit(
        session,
        org_id=ctx.org_id,
        action="run.create",
        target_type="run",
        actor_id=ctx.user_id,
        actor_email=ctx.user.email,
        target_id=str(run.id),
        metadata={"pipeline_id": str(pipeline.id), "version": run.version},
        ip=client_ip(request),
    )
    return RunCreateResponse(
        run=RunOut(
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
            retry_total=run.retry_total,
            error_code=run.error_code,
            error_message=run.error_message,
            created_at=run.created_at,
        ),
        stream_url=f"/v1/runs/{run.id}/events",
    )


@router.get("/projects/{project_id}/lineage")
async def project_lineage(
    project: Project = Depends(get_project_or_404),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Dataset-level lineage graph (FR-027)."""
    return await pipeline_service.lineage_for_project(
        session, org_id=project.org_id, project_id=project.id
    )


@router.put(
    "/pipelines/{pipeline_id}/schedule",
    dependencies=[Depends(verify_csrf)],
)
async def set_schedule(
    pipeline_id: uuid.UUID,
    cron: str = Query(min_length=9, max_length=120),
    ctx: AuthContext = Depends(require_mutation),
    session: AsyncSession = Depends(get_session),
) -> dict[str, str | None]:
    """Attach a 5-field cron expression (FR-028). Invalid cron is rejected."""
    from app.services.scheduler import validate_cron

    if not validate_cron(cron):
        from app.core.errors import ValidationProblem

        raise ValidationProblem("Ekspresi cron tidak valid (butuh 5 field).")
    pipeline = await pipeline_service.get_pipeline_scoped(
        session, org_id=ctx.org_id, pipeline_id=pipeline_id
    )
    pipeline.schedule_cron = cron
    await session.flush()
    return {"pipeline_id": str(pipeline.id), "cron": pipeline.schedule_cron}
