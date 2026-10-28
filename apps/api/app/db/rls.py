"""Tenant context helpers (RLS layer two).

The application ALWAYS filters by ``org_id`` explicitly. This module adds the
Postgres session variable so RLS policies also apply if a query forgets the
filter. See ADR-004.
"""

from __future__ import annotations

import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def set_tenant_context(session: AsyncSession, org_id: uuid.UUID) -> None:
    """Set ``app.current_org_id`` for the current transaction.

    ``SET LOCAL`` is transaction-scoped, so it cannot leak to another request
    through a pooled connection. Must therefore be called INSIDE a transaction.
    """
    if not session.in_transaction():
        await session.begin()
    # Parameter binding is not allowed in SET; the value is a UUID object
    # converted to its canonical string form, never raw user input.
    await session.execute(
        text("SELECT set_config('app.current_org_id', :org_id, true)"),
        {"org_id": str(org_id)},
    )


async def clear_tenant_context(session: AsyncSession) -> None:
    await session.execute(text("SELECT set_config('app.current_org_id', '', true)"))
