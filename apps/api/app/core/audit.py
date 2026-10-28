"""Audit trail writer (FR-043).

Only append. Never store secrets: callers pass ids and small metadata, never
request bodies, keys or tokens.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import request_id_ctx
from app.db.models import AuditLog

# Central list of audited actions so a typo cannot silently drop an audit row.
ACTIONS = frozenset(
    {
        "auth.register",
        "auth.login",
        "auth.login_failed",
        "auth.logout",
        "project.create",
        "project.update",
        "project.delete",
        "source.create",
        "source.update",
        "source.delete",
        "api_key.create",
        "api_key.rotate",
        "api_key.revoke",
        "pipeline.create",
        "pipeline.update",
        "pipeline.version.create",
        "run.create",
        "run.cancel",
        "agent.start",
        "proposal.decision",
        "membership.invite",
        "membership.role_update",
        "membership.remove",
        "budget.update",
        "quality_gate.update",
        "upload.init",
        "upload.complete",
        # Installation-wide (deployment) actions. Separate namespace because
        # they are not scoped to the acting organization's data.
        "installation.llm_update",
        "installation.llm_test",
        "installation.admin_grant",
    }
)


async def record_audit(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    action: str,
    target_type: str,
    actor_kind: str = "user",
    actor_id: uuid.UUID | None = None,
    actor_email: str | None = None,
    target_id: str | None = None,
    metadata: dict[str, Any] | None = None,
    ip: str | None = None,
) -> None:
    """Append one audit row. Unknown actions are still stored but flagged."""
    entry = AuditLog(
        org_id=org_id,
        actor_kind=actor_kind,
        actor_id=actor_id,
        actor_email=actor_email,
        action=action if action in ACTIONS else f"unregistered:{action}",
        target_type=target_type,
        target_id=str(target_id) if target_id is not None else None,
        metadata_json=metadata or {},
        ip=ip,
        request_id=request_id_ctx.get(),
    )
    session.add(entry)
    await session.flush()
