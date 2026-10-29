"""Single entry point for run state transitions (PRD section 9.10).

Every status change of a run MUST go through :func:`transition`. The allowed
edges are an explicit table, not conditionals scattered around the worker.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dataloom_contracts._base import RunStatus

#: (from, to) pairs allowed by PRD section 9.10 state machine.
ALLOWED: frozenset[tuple[str, str]] = frozenset(
    {
        (RunStatus.DRAFT.value, RunStatus.VALIDATED.value),
        (RunStatus.DRAFT.value, RunStatus.CANCELLED.value),
        (RunStatus.VALIDATED.value, RunStatus.QUEUED.value),
        (RunStatus.VALIDATED.value, RunStatus.CANCELLED.value),
        (RunStatus.QUEUED.value, RunStatus.RUNNING.value),
        (RunStatus.QUEUED.value, RunStatus.CANCELLED.value),
        (RunStatus.RUNNING.value, RunStatus.SUCCEEDED.value),
        (RunStatus.RUNNING.value, RunStatus.FAILED.value),
        # A run can be halted by the quality gate without a node crash.
        (RunStatus.RUNNING.value, RunStatus.NEEDS_ATTENTION.value),
        (RunStatus.RUNNING.value, RunStatus.CANCELLED.value),
        (RunStatus.FAILED.value, RunStatus.RETRYING.value),
        # retry exhausted -> needs a human
        (RunStatus.FAILED.value, RunStatus.NEEDS_ATTENTION.value),
        (RunStatus.RETRYING.value, RunStatus.RUNNING.value),
        (RunStatus.RETRYING.value, RunStatus.NEEDS_ATTENTION.value),
        (RunStatus.RETRYING.value, RunStatus.CANCELLED.value),
        # after human/self-heal fix, a new attempt is queued
        (RunStatus.NEEDS_ATTENTION.value, RunStatus.QUEUED.value),
        (RunStatus.NEEDS_ATTENTION.value, RunStatus.CANCELLED.value),
    }
)

TERMINAL: frozenset[str] = frozenset(
    {RunStatus.SUCCEEDED.value, RunStatus.CANCELLED.value}
)


class TransitionError(Exception):
    """Raised when a caller attempts an illegal state change."""


async def transition(
    session: AsyncSession,
    run_id: uuid.UUID,
    to_status: RunStatus,
    *,
    error_code: str | None = None,
    error_message: str | None = None,
    node_id: str | None = None,
) -> str:
    """Apply a legal transition and return the previous status.

    The row is locked with ``SELECT ... FOR UPDATE`` so two workers cannot
    both move the same run.
    """
    from app.db import Run  # local import: avoids cycles in tests

    run = (
        await session.execute(select(Run).where(Run.id == run_id).with_for_update())
    ).scalar_one_or_none()
    if run is None:
        raise TransitionError(f"Run {run_id} tidak ditemukan.")

    current = run.status
    target = to_status.value
    if (current, target) not in ALLOWED:
        raise TransitionError(f"Transisi '{current}' -> '{target}' tidak diizinkan.")

    now = datetime.now(tz=timezone.utc)
    run.status = target
    if target == RunStatus.RUNNING.value:
        run.started_at = run.started_at or now
        run.current_node_id = node_id
    if target in TERMINAL or target in (
        RunStatus.NEEDS_ATTENTION.value,
        RunStatus.FAILED.value,
    ):
        run.finished_at = now
        if run.started_at is not None:
            run.duration_ms = int((now - run.started_at).total_seconds() * 1000)
    if error_code is not None:
        run.error_code = error_code
    if error_message is not None:
        run.error_message = error_message[:2000]

    await session.flush()
    return current


def is_terminal(status: str) -> bool:
    return status in TERMINAL
