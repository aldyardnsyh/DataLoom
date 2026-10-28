"""Usage, budget and audit-log contracts (FR-035, FR-036, FR-042, FR-043).

Every number here comes from ``llm_calls`` / ``audit_logs`` rows. There is no
placeholder field: if a value is unknown it is ``null``, never a fake number
(PRD G4 / NFR-10).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from pydantic import Field

from ._base import ActorKind, AgentType, ReadModel


class LlmCallOut(ReadModel):
    """One metered LLM call (``llm_calls``). ``cost_usd`` is the estimate stored."""

    id: uuid.UUID
    org_id: uuid.UUID
    agent_run_id: uuid.UUID | None = None
    agent_type: AgentType | None = None
    node: str
    provider: str
    model: str
    tokens_in: int = Field(ge=0)
    tokens_out: int = Field(ge=0)
    latency_ms: int = Field(ge=0)
    cost_usd: float = Field(ge=0.0)
    error: str | None = None
    at: datetime


class ModelUsage(ReadModel):
    """Aggregate per model for the donut chart on Usage & Cost."""

    model: str
    provider: str
    calls: int = Field(ge=0)
    tokens_in: int = Field(ge=0)
    tokens_out: int = Field(ge=0)
    cost_usd: float = Field(ge=0.0)


class AgentUsage(ReadModel):
    """Aggregate per agent for the stacked-bar chart."""

    agent_type: AgentType
    calls: int = Field(ge=0)
    tokens: int = Field(ge=0)
    cost_usd: float = Field(ge=0.0)


class DailyUsage(ReadModel):
    day: date
    cost_usd: float = Field(ge=0.0)
    tokens: int = Field(ge=0)
    calls: int = Field(ge=0)


class BudgetOut(ReadModel):
    """Budget state for the BudgetMeter. ``remaining_usd`` may be negative."""

    org_id: uuid.UUID
    period_start: date
    period_end: date
    monthly_budget_usd: float = Field(ge=0.0)
    spent_usd: float = Field(ge=0.0)
    remaining_usd: float
    used_ratio: float = Field(ge=0.0, description="spent / budget; >1 means over.")
    warn_threshold: float = Field(default=0.8, ge=0.0, le=1.0)
    is_exhausted: bool
    per_run_token_limit: int = Field(ge=0)


class UsageSummary(ReadModel):
    """GET /v1/usage payload: real aggregates only."""

    org_id: uuid.UUID
    period_start: date
    period_end: date
    total_calls: int = Field(ge=0)
    total_tokens_in: int = Field(ge=0)
    total_tokens_out: int = Field(ge=0)
    total_cost_usd: float = Field(ge=0.0)
    average_latency_ms: int | None = Field(
        default=None, ge=0, description="null when there are zero calls."
    )
    by_model: list[ModelUsage] = Field(default_factory=list)
    by_agent: list[AgentUsage] = Field(default_factory=list)
    daily: list[DailyUsage] = Field(default_factory=list)
    budget: BudgetOut


class BudgetUpdateRequest(ReadModel):
    """Settings page budget panel."""

    monthly_budget_usd: float | None = Field(default=None, ge=0.0)
    per_run_token_limit: int | None = Field(default=None, ge=0)


class AuditLogOut(ReadModel):
    """``audit_logs`` row. ``target`` is a compact resource locator string."""

    id: uuid.UUID
    org_id: uuid.UUID
    actor_kind: ActorKind
    actor_id: uuid.UUID | None = None
    actor_email: str | None = None
    action: str
    target_type: str
    target_id: str | None = None
    metadata: dict = Field(default_factory=dict)
    ip: str | None = None
    request_id: str | None = None
    at: datetime


class QueueStats(ReadModel):
    """Dashboard 'ingest queue depth' card, from Redis XLEN/XPENDING."""

    source_id: uuid.UUID
    stream: str
    length: int = Field(ge=0)
    pending: int = Field(ge=0)
    consumers: int = Field(ge=0)
    measured_at: datetime


__all__ = [
    "AgentUsage",
    "AuditLogOut",
    "BudgetOut",
    "BudgetUpdateRequest",
    "DailyUsage",
    "LlmCallOut",
    "ModelUsage",
    "QueueStats",
    "UsageSummary",
]
