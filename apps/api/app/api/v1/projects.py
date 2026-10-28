"""Project CRUD (FR-003). Always scoped to ``ctx.org_id``."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Request
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import record_audit
from app.core.errors import Forbidden, NotFound, ValidationProblem
from app.db.models import Pipeline, Project, Run, Source
from app.db.session import get_session
from app.deps import (
    AuthContext,
    client_ip,
    get_org_context,
    get_project_or_404,
    require_mutation,
    verify_csrf,
)
from dataloom_contracts.projects import (
    ProjectCreate,
    ProjectOut,
    ProjectSettings,
    ProjectSummary,
    ProjectUpdate,
)

router = APIRouter(prefix="/projects", tags=["projects"])


def _to_out(project: Project) -> ProjectOut:
    raw = project.settings_json or {}
    return ProjectOut(
        id=project.id,
        org_id=project.org_id,
        name=project.name,
        description=project.description,
        settings=ProjectSettings.model_validate(raw),
        created_at=project.created_at,
        updated_at=project.updated_at,
    )


@router.get("", response_model=list[ProjectSummary])
async def list_projects(
    ctx: AuthContext = Depends(get_org_context),
    session: AsyncSession = Depends(get_session),
) -> list[ProjectSummary]:
    """Project cards with counts. One query per count type, all org-scoped."""
    projects = (
        await session.execute(
            select(Project)
            .where(Project.org_id == ctx.org_id)
            .order_by(Project.updated_at.desc())
        )
    ).scalars().all()

    source_counts = dict(
        (
            await session.execute(
                select(Source.project_id, func.count())
                .where(Source.org_id == ctx.org_id)
                .group_by(Source.project_id)
            )
        ).all()
    )
    pipeline_counts = dict(
        (
            await session.execute(
                select(Pipeline.project_id, func.count())
                .where(Pipeline.org_id == ctx.org_id)
                .group_by(Pipeline.project_id)
            )
        ).all()
    )

    # Latest run status per project (cheap window-free approach: max started_at).
    latest = (
        await session.execute(
            select(Run.project_id, Run.status, Run.started_at)
            .where(Run.org_id == ctx.org_id)
            .order_by(Run.project_id, Run.started_at.desc().nullslast())
        )
    ).all()
    last_status: dict[uuid.UUID, str] = {}
    for project_id, status, _started in latest:
        last_status.setdefault(project_id, status)

    out: list[ProjectSummary] = []
    for project in projects:
        out.append(
            ProjectSummary(
                id=project.id,
                name=project.name,
                description=project.description,
                source_count=int(source_counts.get(project.id, 0)),
                pipeline_count=int(pipeline_counts.get(project.id, 0)),
                last_run_status=last_status.get(project.id),
                updated_at=project.updated_at,
            )
        )
    return out


@router.post(
    "",
    response_model=ProjectOut,
    status_code=201,
    dependencies=[Depends(verify_csrf)],
)
async def create_project(
    payload: ProjectCreate,
    request: Request,
    ctx: AuthContext = Depends(require_mutation),
    session: AsyncSession = Depends(get_session),
) -> ProjectOut:
    project = Project(
        org_id=ctx.org_id,
        name=payload.name.strip(),
        description=payload.description,
        settings_json=payload.settings.model_dump(mode="json"),
    )
    session.add(project)
    await session.flush()
    await record_audit(
        session,
        org_id=ctx.org_id,
        action="project.create",
        target_type="project",
        actor_id=ctx.user_id,
        actor_email=ctx.user.email,
        target_id=str(project.id),
        metadata={"name": project.name},
        ip=client_ip(request),
    )
    return _to_out(project)


@router.get("/{project_id}", response_model=ProjectOut)
async def get_project(
    project: Project = Depends(get_project_or_404),
) -> ProjectOut:
    return _to_out(project)


@router.patch(
    "/{project_id}",
    response_model=ProjectOut,
    dependencies=[Depends(verify_csrf)],
)
async def update_project(
    payload: ProjectUpdate,
    request: Request,
    project: Project = Depends(get_project_or_404),
    ctx: AuthContext = Depends(require_mutation),
    session: AsyncSession = Depends(get_session),
) -> ProjectOut:
    if payload.name is not None:
        project.name = payload.name.strip()
    if payload.description is not None:
        project.description = payload.description
    if payload.settings is not None:
        project.settings_json = payload.settings.model_dump(mode="json")

    await session.flush()
    await record_audit(
        session,
        org_id=ctx.org_id,
        action="project.update",
        target_type="project",
        actor_id=ctx.user_id,
        actor_email=ctx.user.email,
        target_id=str(project.id),
        metadata={"fields": list(payload.model_dump(exclude_unset=True).keys())},
        ip=client_ip(request),
    )
    return _to_out(project)


@router.delete(
    "/{project_id}",
    status_code=204,
    dependencies=[Depends(verify_csrf)],
)
async def delete_project(
    request: Request,
    confirm_name: str,
    project: Project = Depends(get_project_or_404),
    ctx: AuthContext = Depends(require_mutation),
    session: AsyncSession = Depends(get_session),
) -> None:
    """Destructive: the caller must retype the project name (PRD 12.5)."""
    if confirm_name.strip() != project.name:
        raise ValidationProblem(
            "Nama proyek yang diketik tidak cocok. Penghapusan dibatalkan."
        )
    if ctx.role not in ("owner", "admin"):
        raise Forbidden("Hanya owner/admin yang dapat menghapus proyek.")

    await record_audit(
        session,
        org_id=ctx.org_id,
        action="project.delete",
        target_type="project",
        actor_id=ctx.user_id,
        actor_email=ctx.user.email,
        target_id=str(project.id),
        metadata={"name": project.name},
        ip=client_ip(request),
    )
    await session.execute(
        delete(Project).where(Project.id == project.id, Project.org_id == ctx.org_id)
    )
    await session.flush()


@router.get("/{project_id}/settings", response_model=ProjectSettings)
async def get_settings_for_project(
    project: Project = Depends(get_project_or_404),
) -> ProjectSettings:
    return ProjectSettings.model_validate(project.settings_json or {})


@router.put(
    "/{project_id}/settings",
    response_model=ProjectSettings,
    dependencies=[Depends(verify_csrf)],
)
async def update_settings_for_project(
    payload: ProjectSettings,
    request: Request,
    project: Project = Depends(get_project_or_404),
    ctx: AuthContext = Depends(require_mutation),
    session: AsyncSession = Depends(get_session),
) -> ProjectSettings:
    if ctx.role not in ("owner", "admin"):
        raise Forbidden("Hanya owner/admin yang dapat mengubah pengaturan kualitas.")
    project.settings_json = payload.model_dump(mode="json")
    await session.flush()
    await record_audit(
        session,
        org_id=ctx.org_id,
        action="quality_gate.update",
        target_type="project",
        actor_id=ctx.user_id,
        actor_email=ctx.user.email,
        target_id=str(project.id),
        metadata={"settings": project.settings_json},
        ip=client_ip(request),
    )
    return payload
