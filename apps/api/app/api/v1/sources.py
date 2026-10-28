"""Source CRUD (FR-010, FR-014, FR-015) - secrets encrypted at rest."""

from __future__ import annotations

import re
import uuid

from fastapi import APIRouter, Depends, Request
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import record_audit
from app.core.errors import Conflict, NotFound
from app.db.models import Project, Source
from app.db.session import get_session
from app.deps import (
    AuthContext,
    client_ip,
    get_project_or_404,
    require_mutation,
    verify_csrf,
)
from app.services.source_secrets import (
    decrypt_config,
    encrypt_config,
    redact_config,
)
from dataloom_contracts.sources import SourceCreate, SourceOut, SourceUpdate

router = APIRouter(tags=["sources"])

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slug_from(name: str) -> str:
    slug = _SLUG_RE.sub("-", name.strip().lower()).strip("-")[:48]
    return slug or "source"


def _to_out(source: Source) -> SourceOut:
    try:
        public = redact_config(decrypt_config(source.config_enc))
    except Exception:  # noqa: BLE001 - never leak ciphertext to the client
        public = {"error": "config_unreadable"}
    return SourceOut(
        id=source.id,
        org_id=source.org_id,
        project_id=source.project_id,
        name=source.name,
        slug=source.slug,
        type=source.type,
        status=source.status,
        config_public=public,
        ingest_path=f"/v1/ingest/{source.id}",
        last_received_at=source.last_received_at,
        created_at=source.created_at,
        updated_at=source.updated_at,
    )


async def _get_source_scoped(
    source_id: uuid.UUID, project_id: uuid.UUID, org_id: uuid.UUID, session: AsyncSession
) -> Source:
    source = (
        await session.execute(
            select(Source).where(
                Source.id == source_id,
                Source.project_id == project_id,
                Source.org_id == org_id,
            )
        )
    ).scalar_one_or_none()
    if source is None:
        raise NotFound("Sumber tidak ditemukan.")
    return source


@router.get("/projects/{project_id}/sources", response_model=list[SourceOut])
async def list_sources(
    project: Project = Depends(get_project_or_404),
    session: AsyncSession = Depends(get_session),
) -> list[SourceOut]:
    rows = (
        await session.execute(
            select(Source)
            .where(Source.project_id == project.id, Source.org_id == project.org_id)
            .order_by(Source.created_at.desc())
        )
    ).scalars().all()
    return [_to_out(s) for s in rows]


@router.post(
    "/projects/{project_id}/sources",
    response_model=SourceOut,
    status_code=201,
    dependencies=[Depends(verify_csrf)],
)
async def create_source(
    payload: SourceCreate,
    request: Request,
    project: Project = Depends(get_project_or_404),
    ctx: AuthContext = Depends(require_mutation),
    session: AsyncSession = Depends(get_session),
) -> SourceOut:
    org_id = project.org_id
    slug = payload.slug or _slug_from(payload.name)
    clash = (
        await session.execute(
            select(Source.id).where(
                Source.project_id == project.id,
                Source.org_id == org_id,
                Source.slug == slug,
            )
        )
    ).first()
    if clash is not None:
        raise Conflict("Slug sumber sudah dipakai di proyek ini.")

    config_dict = payload.config.model_dump(mode="json")
    source = Source(
        org_id=org_id,
        project_id=project.id,
        name=payload.name.strip(),
        slug=slug,
        type=payload.config.type.value,
        config_enc=encrypt_config(config_dict),
        status="connected",
    )
    session.add(source)
    await session.flush()
    await record_audit(
        session,
        org_id=org_id,
        action="source.create",
        target_type="source",
        actor_id=ctx.user_id,
        actor_email=ctx.user.email,
        target_id=str(source.id),
        metadata={"type": source.type},
        ip=client_ip(request),
    )
    return _to_out(source)


@router.get("/projects/{project_id}/sources/{source_id}", response_model=SourceOut)
async def get_source(
    source_id: uuid.UUID,
    project: Project = Depends(get_project_or_404),
    session: AsyncSession = Depends(get_session),
) -> SourceOut:
    source = await _get_source_scoped(source_id, project.id, project.org_id, session)
    return _to_out(source)


@router.patch(
    "/projects/{project_id}/sources/{source_id}",
    response_model=SourceOut,
    dependencies=[Depends(verify_csrf)],
)
async def update_source(
    source_id: uuid.UUID,
    payload: SourceUpdate,
    request: Request,
    project: Project = Depends(get_project_or_404),
    ctx: AuthContext = Depends(require_mutation),
    session: AsyncSession = Depends(get_session),
) -> SourceOut:
    source = await _get_source_scoped(source_id, project.id, project.org_id, session)
    if payload.name is not None:
        source.name = payload.name.strip()
    if payload.config is not None:
        source.type = payload.config.type.value
        source.config_enc = encrypt_config(payload.config.model_dump(mode="json"))
    await session.flush()
    await record_audit(
        session,
        org_id=project.org_id,
        action="source.update",
        target_type="source",
        actor_id=ctx.user_id,
        actor_email=ctx.user.email,
        target_id=str(source.id),
        ip=client_ip(request),
    )
    return _to_out(source)


@router.delete(
    "/projects/{project_id}/sources/{source_id}",
    status_code=204,
    dependencies=[Depends(verify_csrf)],
)
async def delete_source(
    source_id: uuid.UUID,
    request: Request,
    project: Project = Depends(get_project_or_404),
    ctx: AuthContext = Depends(require_mutation),
    session: AsyncSession = Depends(get_session),
) -> None:
    source = await _get_source_scoped(source_id, project.id, project.org_id, session)
    await record_audit(
        session,
        org_id=project.org_id,
        action="source.delete",
        target_type="source",
        actor_id=ctx.user_id,
        actor_email=ctx.user.email,
        target_id=str(source.id),
        ip=client_ip(request),
    )
    await session.execute(
        delete(Source).where(Source.id == source.id, Source.org_id == project.org_id)
    )
