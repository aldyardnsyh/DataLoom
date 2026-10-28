"""Authentication and organization bootstrap (FR-001, FR-002, D-01).

D-01: registration creates exactly one organization and an owner membership.
Tables stay multi-tenant, so a second org is possible later without migration.
"""

from __future__ import annotations

import re
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.errors import Conflict, Unauthorized
from app.core.security import (
    hash_password,
    needs_rehash,
    new_csrf_token,
    new_session_token,
    sha256_hex,
    verify_password,
)
from app.db.models import Membership, Organization, Session, User
from dataloom_contracts._base import Role

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify(name: str) -> str:
    base = _SLUG_RE.sub("-", name.strip().lower()).strip("-") or "org"
    return f"{base[:60]}-{secrets.token_hex(3)}"


async def _unique_slug(session: AsyncSession, name: str) -> str:
    for _ in range(5):
        candidate = slugify(name)
        exists = (
            await session.execute(
                select(Organization.id).where(Organization.slug == candidate)
            )
        ).first()
        if exists is None:
            return candidate
    return f"org-{uuid.uuid4().hex[:12]}"


async def get_user_by_email(session: AsyncSession, email: str) -> User | None:
    return (
        await session.execute(select(User).where(User.email == email.strip().lower()))
    ).scalar_one_or_none()


async def register_user(
    session: AsyncSession, *, email: str, password: str, name: str, org_name: str
) -> tuple[User, Organization, Membership]:
    """Create user + org + owner membership atomically.

    The caller owns the transaction; a unique-violation on email surfaces as
    409 (checked here AND enforced by the DB unique constraint).
    """
    normalized = email.strip().lower()
    if await get_user_by_email(session, normalized) is not None:
        raise Conflict("Email sudah terdaftar.")

    user = User(
        email=normalized,
        password_hash=hash_password(password),
        name=name.strip(),
    )
    session.add(user)
    await session.flush()

    org = Organization(
        name=org_name.strip(),
        slug=await _unique_slug(session, org_name),
    )
    session.add(org)
    await session.flush()

    membership = Membership(org_id=org.id, user_id=user.id, role=Role.OWNER.value)
    session.add(membership)
    await session.flush()

    return user, org, membership


async def authenticate(
    session: AsyncSession, *, email: str, password: str
) -> tuple[User, Organization, Membership]:
    """Verify credentials. Generic message to avoid user enumeration."""
    user = await get_user_by_email(session, email)
    if user is None or not user.is_active:
        # Hash a dummy value to keep timing roughly constant.
        verify_password(password, "$argon2id$v=19$m=65536,t=3,p=4$" + "0" * 22 + "$" + "0" * 43)
        raise Unauthorized("Email atau kata sandi salah.")
    if not verify_password(password, user.password_hash):
        raise Unauthorized("Email atau kata sandi salah.")
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)

    membership = (
        await session.execute(
            select(Membership).where(Membership.user_id == user.id).limit(1)
        )
    ).scalar_one_or_none()
    if membership is None:
        raise Unauthorized("Akun tidak terhubung ke organisasi mana pun.")
    org = (
        await session.execute(
            select(Organization).where(Organization.id == membership.org_id)
        )
    ).scalar_one_or_none()
    if org is None:
        raise Unauthorized("Organisasi tidak ditemukan.")

    user.last_login_at = datetime.now(tz=timezone.utc)
    return user, org, membership


async def create_session(
    session: AsyncSession, *, user: User, org_id: uuid.UUID, ip: str | None, ua: str | None
) -> tuple[Session, str, str]:
    """Create a session row. Returns ``(row, plaintext_session, csrf_token)``."""
    settings = get_settings()
    plaintext = new_session_token()
    csrf = new_csrf_token()
    row = Session(
        org_id=org_id,
        user_id=user.id,
        token_hash=sha256_hex(plaintext),
        csrf_token_hash=sha256_hex(csrf),
        expires_at=datetime.now(tz=timezone.utc)
        + timedelta(hours=settings.SESSION_TTL_HOURS),
        user_agent=(ua or "")[:255] or None,
        ip=(ip or "")[:64] or None,
    )
    session.add(row)
    await session.flush()
    return row, plaintext, csrf


async def revoke_session(session: AsyncSession, row: Session) -> None:
    row.revoked_at = datetime.now(tz=timezone.utc)
    await session.flush()


def session_ttl_seconds() -> int:
    return get_settings().SESSION_TTL_HOURS * 3600
