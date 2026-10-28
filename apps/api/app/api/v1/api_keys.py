"""API key lifecycle (FR-004).

Plaintext is returned exactly once. Only owner/admin may create/rotate/revoke.
Revoking immediately clears the ingest lookup cache so the key stops working.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import record_audit
from app.core.errors import Forbidden, NotFound
from app.core.security import generate_api_key
from app.db.models import ApiKey, Project
from app.db.session import get_session
from app.deps import (
    AuthContext,
    client_ip,
    get_project_or_404,
    require_mutation,
    verify_csrf,
)
from app.services.ingest_service import invalidate_api_key_cache
from dataloom_contracts.sources import (
    ApiKeyCreate,
    ApiKeyCreated,
    ApiKeyOut,
    ApiKeyRotateRequest,
)

router = APIRouter(tags=["api_keys"])


def _to_out(key: ApiKey) -> ApiKeyOut:
    return ApiKeyOut(
        id=key.id,
        org_id=key.org_id,
        project_id=key.project_id,
        name=key.name,
        prefix=key.prefix,
        created_at=key.created_at,
        last_used_at=key.last_used_at,
        revoked_at=key.revoked_at,
    )


def _require_key_admin(ctx: AuthContext) -> None:
    if ctx.role not in ("owner", "admin"):
        raise Forbidden("Hanya owner/admin yang dapat mengelola API key.")


async def _get_key(
    key_id: uuid.UUID, project: Project, session: AsyncSession
) -> ApiKey:
    key = (
        await session.execute(
            select(ApiKey).where(
                ApiKey.id == key_id,
                ApiKey.project_id == project.id,
                ApiKey.org_id == project.org_id,
            )
        )
    ).scalar_one_or_none()
    if key is None:
        raise NotFound("API key tidak ditemukan.")
    return key


@router.get("/projects/{project_id}/api-keys", response_model=list[ApiKeyOut])
async def list_api_keys(
    project: Project = Depends(get_project_or_404),
    session: AsyncSession = Depends(get_session),
) -> list[ApiKeyOut]:
    rows = (
        await session.execute(
            select(ApiKey)
            .where(ApiKey.project_id == project.id, ApiKey.org_id == project.org_id)
            .order_by(ApiKey.created_at.desc())
        )
    ).scalars().all()
    return [_to_out(k) for k in rows]


@router.post(
    "/projects/{project_id}/api-keys",
    response_model=ApiKeyCreated,
    status_code=201,
    dependencies=[Depends(verify_csrf)],
)
async def create_api_key(
    payload: ApiKeyCreate,
    request: Request,
    project: Project = Depends(get_project_or_404),
    ctx: AuthContext = Depends(require_mutation),
    session: AsyncSession = Depends(get_session),
) -> ApiKeyCreated:
    _require_key_admin(ctx)
    plaintext, prefix, key_hash = generate_api_key()
    key = ApiKey(
        org_id=project.org_id,
        project_id=project.id,
        name=payload.name.strip(),
        prefix=prefix,
        key_hash=key_hash,
    )
    session.add(key)
    await session.flush()
    await record_audit(
        session,
        org_id=project.org_id,
        action="api_key.create",
        target_type="api_key",
        actor_id=ctx.user_id,
        actor_email=ctx.user.email,
        target_id=str(key.id),
        metadata={"prefix": key.prefix},
        ip=client_ip(request),
    )
    return ApiKeyCreated(key=_to_out(key), plaintext_key=plaintext)


@router.post(
    "/projects/{project_id}/api-keys/{key_id}/rotate",
    response_model=ApiKeyCreated,
    dependencies=[Depends(verify_csrf)],
)
async def rotate_api_key(
    key_id: uuid.UUID,
    payload: ApiKeyRotateRequest,
    request: Request,
    project: Project = Depends(get_project_or_404),
    ctx: AuthContext = Depends(require_mutation),
    session: AsyncSession = Depends(get_session),
) -> ApiKeyCreated:
    """Revoke the old key and mint a new one in the same transaction."""
    _require_key_admin(ctx)
    old = await _get_key(key_id, project, session)
    if old.revoked_at is not None:
        raise NotFound("API key sudah dicabut.")

    old.revoked_at = datetime.now(tz=timezone.utc)
    invalidate_api_key_cache(old.prefix)

    plaintext, prefix, key_hash = generate_api_key()
    new_key = ApiKey(
        org_id=project.org_id,
        project_id=project.id,
        name=old.name,
        prefix=prefix,
        key_hash=key_hash,
    )
    session.add(new_key)
    await session.flush()
    await record_audit(
        session,
        org_id=project.org_id,
        action="api_key.rotate",
        target_type="api_key",
        actor_id=ctx.user_id,
        actor_email=ctx.user.email,
        target_id=str(new_key.id),
        metadata={"old_key_id": str(old.id), "old_prefix": old.prefix},
        ip=client_ip(request),
    )
    return ApiKeyCreated(key=_to_out(new_key), plaintext_key=plaintext)


@router.delete(
    "/projects/{project_id}/api-keys/{key_id}",
    status_code=204,
    dependencies=[Depends(verify_csrf)],
)
async def revoke_api_key(
    key_id: uuid.UUID,
    request: Request,
    project: Project = Depends(get_project_or_404),
    ctx: AuthContext = Depends(require_mutation),
    session: AsyncSession = Depends(get_session),
) -> None:
    _require_key_admin(ctx)
    key = await _get_key(key_id, project, session)
    key.revoked_at = datetime.now(tz=timezone.utc)
    invalidate_api_key_cache(key.prefix)  # immediate rejection (FR-004)
    await session.flush()
    await record_audit(
        session,
        org_id=project.org_id,
        action="api_key.revoke",
        target_type="api_key",
        actor_id=ctx.user_id,
        actor_email=ctx.user.email,
        target_id=str(key.id),
        metadata={"prefix": key.prefix},
        ip=client_ip(request),
    )
