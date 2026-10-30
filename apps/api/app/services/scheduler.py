"""Cron scheduling for pipelines (FR-028).

Minimal by design (D-03): a single in-process APScheduler instance that creates
runs for pipelines with a cron expression, respecting the project's
``max_concurrent_runs``. Multi-replica safety via Redis leader election
(``SET NX PX`` + refresh): only the lock holder runs ``_tick``; followers
skip with a log. Fail-open: if Redis is down, the replica proceeds so a
single-replica deployment keeps working.
"""

from __future__ import annotations

import logging
import uuid

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import select

from app.db.models import Pipeline, Project, Run
from app.db.session import get_session_factory
from app.services.run_service import enqueue_run

logger = logging.getLogger("dataloom.scheduler")

_scheduler: AsyncIOScheduler | None = None

#: Redis key for scheduler leader election (multi-replica safety).
#: Owner refreshes TTL on every tick; TTL > interval so a live leader never loses it.
LEADER_KEY = "dataloom:scheduler:leader"
LEADER_TTL_SECONDS = 90

_leader_token: str = uuid.uuid4().hex


async def _ensure_leader() -> bool:
    """Try to acquire/refresh the leader lock. ``True`` = this replica is leader."""
    global _leader_token
    try:
        from app.core.ratelimit import get_redis

        client = get_redis()
        current = await client.get(LEADER_KEY)
        if current is None:
            acquired = await client.set(
                LEADER_KEY, _leader_token, nx=True, px=LEADER_TTL_SECONDS * 1000
            )
            if acquired:
                logger.info("Scheduler menjadi leader.")
                return True
            return False
        if current == _leader_token:
            await client.pexpire(LEADER_KEY, LEADER_TTL_SECONDS * 1000)
            return True
        logger.info("Scheduler follower: lewati tick (leader aktif).")
        return False
    except Exception as exc:  # noqa: BLE001 - Redis down: single-replica tetap jalan (fail open)
        logger.warning("Lock leader tak tersedia, jalan sebagai leader: %s", exc)
        return True


def validate_cron(expression: str) -> bool:
    """Accept standard 5-field cron only. Returns ``False`` instead of raising."""
    fields = expression.split()
    if len(fields) != 5:
        return False
    try:
        CronTrigger.from_crontab(expression)
        return True
    except (ValueError, TypeError):
        return False


async def _tick() -> None:
    """Evaluate due pipelines and enqueue runs (idempotent per minute)."""
    if not await _ensure_leader():
        return
    factory = get_session_factory()
    async with factory() as session:
        pipelines = (
            await session.execute(
                select(Pipeline).where(
                    Pipeline.schedule_cron.is_not(None), Pipeline.is_active.is_(True)
                )
            )
        ).scalars().all()

        for pipeline in pipelines:
            cron = pipeline.schedule_cron
            if not cron or not validate_cron(cron):
                continue
            project = (
                await session.execute(
                    select(Project).where(Project.id == pipeline.project_id)
                )
            ).scalar_one_or_none()
            if project is None:
                continue
            try:
                await enqueue_run(
                    session,
                    org_id=pipeline.org_id,
                    pipeline_id=pipeline.id,
                    user_id=uuid.UUID(int=0),  # system-triggered
                    trigger="schedule",
                    idempotency_key=f"sched:{pipeline.id}:{_minute_bucket()}",
                )
                await session.commit()
            except Exception as exc:  # noqa: BLE001 - one pipeline failing must not stop others
                await session.rollback()
                logger.info("Jadwal pipeline %s dilewati: %s", pipeline.id, exc)


def _minute_bucket() -> str:
    from datetime import datetime, timezone

    now = datetime.now(tz=timezone.utc)
    return now.strftime("%Y%m%dT%H%M")


def start_scheduler() -> AsyncIOScheduler | None:
    """Start the scheduler if enabled. Returns ``None`` when disabled."""
    from app.config import get_settings

    global _scheduler
    if not get_settings().SCHEDULER_ENABLED:
        logger.info("Scheduler dinonaktifkan (SCHEDULER_ENABLED=false).")
        return None
    if _scheduler is not None:
        return _scheduler
    _scheduler = AsyncIOScheduler(timezone="UTC")
    _scheduler.add_job(_tick, "interval", minutes=1, id="pipeline-schedule-tick", replace_existing=True)
    _scheduler.start()
    logger.info("Scheduler aktif (tick per menit).")
    return _scheduler


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
