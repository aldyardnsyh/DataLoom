"""Usage, budget and audit API (FR-035, FR-036, FR-042, FR-043).

Every number is an aggregate over ``llm_calls`` / ``audit_logs`` / Redis. When
there is no data the aggregate is ``0`` and latency is ``null`` — never a
placeholder number (PRD G4, NFR-10).
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import record_audit
from app.core.errors import Forbidden
from app.core.ratelimit import get_redis, stream_key
from app.db.models import AgentRun, AuditLog, LlmCall, Organization, Project, Source
from app.db.session import get_session
from app.deps import AuthContext, client_ip, get_org_context, require_mutation, verify_csrf
from dataloom_contracts.usage import (
    AgentUsage,
    AuditLogOut,
    BudgetOut,
    BudgetUpdateRequest,
    DailyUsage,
    LlmCallOut,
    ModelUsage,
    QueueStats,
    UsageSummary,
)

router = APIRouter(tags=["usage"])


def _month_bounds(now: datetime | None = None) -> tuple[date, date]:
    today = (now or datetime.now(tz=timezone.utc)).date()
    start = today.replace(day=1)
    # End is exclusive: first day of next month.
    end = (start + timedelta(days=32)).replace(day=1)
    return start, end


def _budget_out(org: Organization, spent: float) -> BudgetOut:
    start, end = _month_bounds()
    budget = float(org.llm_budget_usd_month)
    remaining = budget - spent
    ratio = (spent / budget) if budget > 0 else 0.0
    return BudgetOut(
        org_id=org.id,
        period_start=start,
        period_end=end,
        monthly_budget_usd=budget,
        spent_usd=round(spent, 6),
        remaining_usd=round(remaining, 6),
        used_ratio=round(ratio, 6),
        warn_threshold=0.8,
        is_exhausted=ratio >= 1.0,
        per_run_token_limit=int(org.per_run_token_limit),
    )


async def _org(session: AsyncSession, org_id: uuid.UUID) -> Organization:
    return (
        await session.execute(select(Organization).where(Organization.id == org_id))
    ).scalar_one()


async def _spent(session: AsyncSession, org_id: uuid.UUID) -> float:
    start, end = _month_bounds()
    total = (
        await session.execute(
            select(func.coalesce(func.sum(LlmCall.cost_usd), 0)).where(
                LlmCall.org_id == org_id,
                LlmCall.created_at >= datetime.combine(start, time.min, tzinfo=timezone.utc),
                LlmCall.created_at < datetime.combine(end, time.min, tzinfo=timezone.utc),
            )
        )
    ).scalar()
    return float(total or 0.0)


@router.get("/usage", response_model=UsageSummary)
async def usage_summary(
    ctx: AuthContext = Depends(get_org_context),
    session: AsyncSession = Depends(get_session),
) -> UsageSummary:
    """Real aggregates for the Usage & Cost page (FR-036)."""
    start, end = _month_bounds()
    start_dt = datetime.combine(start, time.min, tzinfo=timezone.utc)
    end_dt = datetime.combine(end, time.min, tzinfo=timezone.utc)

    rows = list(
        (
            await session.execute(
                select(LlmCall).where(
                    LlmCall.org_id == ctx.org_id,
                    LlmCall.created_at >= start_dt,
                    LlmCall.created_at < end_dt,
                )
            )
        ).scalars()
    )

    by_model: dict[tuple[str, str], dict[str, float]] = defaultdict(lambda: {"calls": 0, "tokens_in": 0, "tokens_out": 0, "cost": 0.0})
    by_agent_run: dict[uuid.UUID | None, dict[str, float]] = defaultdict(lambda: {"calls": 0, "tokens": 0, "cost": 0.0})
    by_day: dict[date, dict[str, float]] = defaultdict(lambda: {"cost": 0.0, "tokens": 0, "calls": 0})

    total_in = 0
    total_out = 0
    total_cost = 0.0
    latencies: list[int] = []

    for call in rows:
        total_in += call.tokens_in
        total_out += call.tokens_out
        total_cost += float(call.cost_usd)
        latencies.append(call.latency_ms)

        model_bucket = by_model[(call.model, call.provider)]
        model_bucket["calls"] += 1
        model_bucket["tokens_in"] += call.tokens_in
        model_bucket["tokens_out"] += call.tokens_out
        model_bucket["cost"] += float(call.cost_usd)

        agent_bucket = by_agent_run[call.agent_run_id]
        agent_bucket["calls"] += 1
        agent_bucket["tokens"] += call.tokens_in + call.tokens_out
        agent_bucket["cost"] += float(call.cost_usd)

        day_bucket = by_day[call.created_at.date()]
        day_bucket["cost"] += float(call.cost_usd)
        day_bucket["tokens"] += call.tokens_in + call.tokens_out
        day_bucket["calls"] += 1

    # Resolve agent types for per-agent aggregates in one query.
    agent_run_ids = [key for key in by_agent_run if key is not None]
    agent_types: dict[uuid.UUID, str] = {}
    if agent_run_ids:
        for run_id, agent_type in (
            await session.execute(
                select(AgentRun.id, AgentRun.agent_type).where(AgentRun.id.in_(agent_run_ids))
            )
        ).all():
            agent_types[run_id] = agent_type

    org = await _org(session, ctx.org_id)
    spent = await _spent(session, ctx.org_id)

    # Ingest queue depth: summed XLEN over this org's sources (real Redis data).
    depth = 0
    try:
        sources = list(
            (
                await session.execute(
                    select(Source).where(Source.org_id == ctx.org_id, Source.type.in_(["webhook", "api"]))
                )
            ).scalars()
        )
        redis = get_redis()
        for source in sources:
            depth += int(await redis.xlen(stream_key(str(ctx.org_id), str(source.id))))
    except Exception:  # noqa: BLE001 - queue depth is optional telemetry
        depth = -1

    return UsageSummary(
        org_id=ctx.org_id,
        period_start=start,
        period_end=end,
        total_calls=len(rows),
        total_tokens_in=total_in,
        total_tokens_out=total_out,
        total_cost_usd=round(total_cost, 6),
        average_latency_ms=(sum(latencies) // len(latencies)) if latencies else None,
        by_model=[
            ModelUsage(
                model=model,
                provider=provider,
                calls=int(values["calls"]),
                tokens_in=int(values["tokens_in"]),
                tokens_out=int(values["tokens_out"]),
                cost_usd=round(values["cost"], 6),
            )
            for (model, provider), values in sorted(by_model.items())
        ],
        by_agent=[
            AgentUsage(
                agent_type=agent_types.get(run_id, "supervisor") if run_id else "supervisor",
                calls=int(values["calls"]),
                tokens=int(values["tokens"]),
                cost_usd=round(values["cost"], 6),
            )
            for run_id, values in by_agent_run.items()
        ],
        daily=[
            DailyUsage(
                day=day,
                cost_usd=round(values["cost"], 6),
                tokens=int(values["tokens"]),
                calls=int(values["calls"]),
            )
            for day, values in sorted(by_day.items())
        ],
        budget=_budget_out(org, spent),
    )


@router.get("/usage/llm-calls", response_model=list[LlmCallOut])
async def list_llm_calls(
    ctx: AuthContext = Depends(get_org_context),
    session: AsyncSession = Depends(get_session),
    limit: int = Query(default=100, ge=1, le=500),
) -> list[LlmCallOut]:
    rows = list(
        (
            await session.execute(
                select(LlmCall)
                .where(LlmCall.org_id == ctx.org_id)
                .order_by(LlmCall.created_at.desc())
                .limit(limit)
            )
        ).scalars()
    )
    return [
        LlmCallOut(
            id=row.id,
            org_id=row.org_id,
            agent_run_id=row.agent_run_id,
            agent_type=None,
            node=row.node,
            provider=row.provider,
            model=row.model,
            tokens_in=row.tokens_in,
            tokens_out=row.tokens_out,
            latency_ms=row.latency_ms,
            cost_usd=float(row.cost_usd),
            error=row.error,
            at=row.created_at,
        )
        for row in rows
    ]


@router.patch(
    "/usage/budget",
    response_model=BudgetOut,
    dependencies=[Depends(verify_csrf)],
)
async def update_budget(
    payload: BudgetUpdateRequest,
    request: Request,
    ctx: AuthContext = Depends(require_mutation),
    session: AsyncSession = Depends(get_session),
) -> BudgetOut:
    """Budget settings (FR-035). Owner/admin only."""
    if ctx.role not in ("owner", "admin"):
        raise Forbidden("Hanya owner/admin yang dapat mengubah anggaran.")
    org = await _org(session, ctx.org_id)
    if payload.monthly_budget_usd is not None:
        org.llm_budget_usd_month = payload.monthly_budget_usd
    if payload.per_run_token_limit is not None:
        org.per_run_token_limit = payload.per_run_token_limit
    await session.flush()
    await record_audit(
        session,
        org_id=ctx.org_id,
        action="budget.update",
        target_type="organization",
        actor_id=ctx.user_id,
        actor_email=ctx.user.email,
        target_id=str(org.id),
        metadata={
            "monthly_budget_usd": float(org.llm_budget_usd_month),
            "per_run_token_limit": int(org.per_run_token_limit),
        },
        ip=client_ip(request),
    )
    return _budget_out(org, await _spent(session, ctx.org_id))


@router.get("/usage/queue", response_model=list[QueueStats])
async def queue_stats(
    ctx: AuthContext = Depends(get_org_context),
    session: AsyncSession = Depends(get_session),
) -> list[QueueStats]:
    """Metric-nyata dashboard: XLEN/XPENDING per ingest stream (FR-042)."""
    sources = list(
        (
            await session.execute(
                select(Source).where(Source.org_id == ctx.org_id, Source.type.in_(["webhook", "api"]))
            )
        ).scalars()
    )
    now = datetime.now(tz=timezone.utc)
    stats: list[QueueStats] = []
    redis = get_redis()
    for source in sources:
        stream = stream_key(str(ctx.org_id), str(source.id))
        try:
            length = int(await redis.xlen(stream))
            pending = 0
            consumers = 0
            try:
                info = await redis.xpending(stream, "dataloom-workers")
                if isinstance(info, dict):
                    pending = int(info.get("pending", 0) or 0)
            except Exception:  # noqa: BLE001 - group may not exist yet
                pending = 0
            try:
                consumer_rows = await redis.xinfo_consumers(stream, "dataloom-workers")
                consumers = len(consumer_rows or [])
            except Exception:  # noqa: BLE001
                consumers = 0
        except Exception:  # noqa: BLE001
            continue
        stats.append(
            QueueStats(
                source_id=source.id,
                stream=stream,
                length=length,
                pending=pending,
                consumers=consumers,
                measured_at=now,
            )
        )
    return stats


@router.get("/settings/audit-logs", response_model=list[AuditLogOut])
async def list_audit_logs(
    ctx: AuthContext = Depends(get_org_context),
    session: AsyncSession = Depends(get_session),
    limit: int = Query(default=100, ge=1, le=500),
) -> list[AuditLogOut]:
    """Audit trail (FR-043). Read-only for every role including viewer."""
    rows = list(
        (
            await session.execute(
                select(AuditLog)
                .where(AuditLog.org_id == ctx.org_id)
                .order_by(AuditLog.at.desc())
                .limit(limit)
            )
        ).scalars()
    )
    return [
        AuditLogOut(
            id=row.id,
            org_id=row.org_id,
            actor_kind=row.actor_kind,
            actor_id=row.actor_id,
            actor_email=row.actor_email,
            action=row.action,
            target_type=row.target_type,
            target_id=row.target_id,
            metadata=row.metadata_json or {},
            ip=row.ip,
            request_id=row.request_id,
            at=row.at,
        )
        for row in rows
    ]

