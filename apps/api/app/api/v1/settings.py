"""Settings API: members/roles (FR-002, FR-043)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Request
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import record_audit
from app.core.errors import Conflict, Forbidden, NotFound
from app.db.models import Membership, Session, User
from app.db.session import get_session
from app.deps import AuthContext, client_ip, get_org_context, require_mutation, verify_csrf
from dataloom_contracts._base import Role
from dataloom_contracts.auth import MembershipOut, UpdateMembershipRoleRequest

router = APIRouter(prefix="/settings", tags=["settings"])


def _out(membership: Membership, user: User) -> MembershipOut:
    return MembershipOut(
        id=membership.id,
        org_id=membership.org_id,
        user_id=membership.user_id,
        role=membership.role,
        user_email=user.email,
        user_name=user.name,
        created_at=membership.created_at,
    )


@router.get("/members", response_model=list[MembershipOut])
async def list_members(
    ctx: AuthContext = Depends(get_org_context),
    session: AsyncSession = Depends(get_session),
) -> list[MembershipOut]:
    rows = (
        await session.execute(
            select(Membership, User)
            .join(User, User.id == Membership.user_id)
            .where(Membership.org_id == ctx.org_id)
            .order_by(Membership.created_at.asc())
        )
    ).all()
    return [_out(membership, user) for membership, user in rows]


@router.patch(
    "/members/{membership_id}",
    response_model=MembershipOut,
    dependencies=[Depends(verify_csrf)],
)
async def update_role(
    membership_id: uuid.UUID,
    payload: UpdateMembershipRoleRequest,
    request: Request,
    ctx: AuthContext = Depends(require_mutation),
    session: AsyncSession = Depends(get_session),
) -> MembershipOut:
    """Owner/admin may change roles; the last owner can never be demoted."""
    if ctx.role not in ("owner", "admin"):
        raise Forbidden("Hanya owner/admin yang dapat mengubah peran.")

    row = (
        await session.execute(
            select(Membership, User)
            .join(User, User.id == Membership.user_id)
            .where(Membership.id == membership_id, Membership.org_id == ctx.org_id)
        )
    ).first()
    if row is None:
        raise NotFound("Anggota tidak ditemukan.")
    membership, user = row

    if membership.role == Role.OWNER.value and payload.role is not Role.OWNER:
        owners = (
            await session.execute(
                select(Membership.id).where(
                    Membership.org_id == ctx.org_id, Membership.role == Role.OWNER.value
                )
            )
        ).all()
        if len(owners) <= 1:
            raise Conflict("Organisasi harus memiliki minimal satu owner.")

    membership.role = payload.role.value
    await session.flush()
    await record_audit(
        session,
        org_id=ctx.org_id,
        action="membership.role_update",
        target_type="membership",
        actor_id=ctx.user_id,
        actor_email=ctx.user.email,
        target_id=str(membership.id),
        metadata={"role": membership.role},
        ip=client_ip(request),
    )
    return _out(membership, user)


@router.delete(
    "/members/{membership_id}",
    status_code=204,
    dependencies=[Depends(verify_csrf)],
)
async def remove_member(
    membership_id: uuid.UUID,
    request: Request,
    ctx: AuthContext = Depends(require_mutation),
    session: AsyncSession = Depends(get_session),
) -> None:
    """Remove a member and revoke their sessions in this org (fail closed)."""
    if ctx.role not in ("owner", "admin"):
        raise Forbidden("Hanya owner/admin yang dapat menghapus anggota.")

    membership = (
        await session.execute(
            select(Membership).where(
                Membership.id == membership_id, Membership.org_id == ctx.org_id
            )
        )
    ).scalar_one_or_none()
    if membership is None:
        raise NotFound("Anggota tidak ditemukan.")
    if membership.role == Role.OWNER.value:
        raise Conflict("Owner tidak dapat dihapus dari organisasi.")

    await record_audit(
        session,
        org_id=ctx.org_id,
        action="membership.remove",
        target_type="membership",
        actor_id=ctx.user_id,
        actor_email=ctx.user.email,
        target_id=str(membership.id),
        ip=client_ip(request),
    )
    await session.execute(delete(Membership).where(Membership.id == membership.id))
    await session.execute(
        delete(Session).where(
            Session.user_id == membership.user_id, Session.org_id == ctx.org_id
        )
    )
    await session.flush()
