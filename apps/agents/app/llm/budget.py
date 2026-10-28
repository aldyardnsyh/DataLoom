"""Budget guard (FR-035).

Three independent limits, checked BEFORE every LLM call:

1. **Per-run token budget** - ``agent_runs.budget_tokens`` (initialised from
   ``organizations.per_run_token_limit``). ``agent_runs.tokens_used`` is
   incremented by the caller after each call.
2. **Per-org monthly USD budget** - ``organizations.llm_budget_usd_month``
   compared against ``SUM(llm_calls.cost_usd)`` for the current month.
3. **Concurrency cap** - ``AGENT_MAX_CONCURRENT_PER_ORG`` (PRD A0: "maks 3 run
   agent konkuren per org"). Enforced by the runner before the graph starts,
   exposed here so both limits live in one place.

Prices come from ``LLM_PRICE_IN_PER_1K`` / ``LLM_PRICE_OUT_PER_1K``. They are
env placeholders: ``0.0`` means "not configured", which makes
``cost_usd`` a recorded 0.0 (an honest "unknown"), never a guess.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import AgentSettings, get_settings

logger = logging.getLogger("dataloom.agents.budget")


class BudgetExceeded(Exception):
    """Raised before a call that would exceed a configured limit.

    ``kind`` is one of ``tokens``, ``usd``, ``concurrency``. The runner maps it
    to ``AgentRunStatus.BUDGET_EXCEEDED`` (never to ``FAILED``: an exhausted
    budget is a declared stop, not a crash).
    """

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind
        self.safe_message = message


@dataclass(slots=True)
class BudgetState:
    """Snapshot of the limits for one org / one run."""

    org_id: uuid.UUID
    monthly_budget_usd: float
    spent_usd_month: float
    per_run_token_limit: int
    tokens_used_run: int
    price_in_per_1k: float
    price_out_per_1k: float

    @property
    def remaining_usd(self) -> float:
        return round(self.monthly_budget_usd - self.spent_usd_month, 6)

    @property
    def remaining_tokens(self) -> int:
        return max(0, self.per_run_token_limit - self.tokens_used_run)


def cost_usd(
    *, tokens_in: int, tokens_out: int, settings: AgentSettings | None = None
) -> float:
    """Estimated USD cost of one call from the configured price sheet.

    Returns ``0.0`` when prices are not configured -- that is a documented
    "unknown", not a claim of zero cost. Rounding to 6 decimals matches
    ``NUMERIC(12,6)``.
    """
    cfg = settings or get_settings()
    total = (
        (tokens_in / 1000.0) * cfg.LLM_PRICE_IN_PER_1K
        + (tokens_out / 1000.0) * cfg.LLM_PRICE_OUT_PER_1K
    )
    return round(max(0.0, total), 6)


def estimate_call_usd(estimated_tokens_in: int, max_tokens_out: int, settings: AgentSettings | None = None) -> float:
    """Worst-case cost estimate used by :meth:`BudgetGuard.ensure_can_spend`."""
    return cost_usd(
        tokens_in=estimated_tokens_in,
        tokens_out=max_tokens_out,
        settings=settings,
    )


class BudgetGuard:
    """Reads the org/run limits and refuses calls that would exceed them.

    The guard is read-only: it never writes ``llm_calls`` or mutates
    ``agent_runs``. :func:`app.graphs.base.call_llm_logged` owns the write so
    there is exactly one place where a metered call is recorded.
    """

    def __init__(
        self,
        session: AsyncSession,
        *,
        org_id: uuid.UUID,
        agent_run_id: uuid.UUID | None = None,
        settings: AgentSettings | None = None,
    ) -> None:
        self.session = session
        self.org_id = org_id
        self.agent_run_id = agent_run_id
        self.settings = settings or get_settings()

    # ------------------------------------------------------------------
    # reads
    # ------------------------------------------------------------------
    async def _org_limits(self) -> tuple[float, int]:
        from app.db.models import Organization

        row = (
            await self.session.execute(
                select(Organization.llm_budget_usd_month, Organization.per_run_token_limit)
                .where(Organization.id == self.org_id)
            )
        ).first()
        if row is None:
            # An unknown org is a bug, not a reason to spend money.
            raise BudgetExceeded(
                "usd", "Organisasi tidak ditemukan; anggaran tidak dapat diverifikasi."
            )
        monthly = float(row[0] or 0)
        per_run = int(row[1] or 0)
        return monthly, per_run

    async def spent_this_month(self) -> float:
        """``SUM(llm_calls.cost_usd)`` for the current calendar month (UTC)."""
        from app.db.models import LlmCall

        now = datetime.now(tz=timezone.utc)
        period_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        total = (
            await self.session.execute(
                select(func.coalesce(func.sum(LlmCall.cost_usd), 0)).where(
                    LlmCall.org_id == self.org_id,
                    LlmCall.created_at >= period_start,
                )
            )
        ).scalar()
        return float(total or 0.0)

    async def run_tokens_used(self) -> int:
        if self.agent_run_id is None:
            return 0
        from app.db.models import AgentRun

        used = (
            await self.session.execute(
                select(AgentRun.tokens_used).where(
                    AgentRun.id == self.agent_run_id, AgentRun.org_id == self.org_id
                )
            )
        ).scalar_one_or_none()
        return int(used or 0)

    async def state(self) -> BudgetState:
        monthly, per_run = await self._org_limits()
        return BudgetState(
            org_id=self.org_id,
            monthly_budget_usd=monthly,
            spent_usd_month=await self.spent_this_month(),
            per_run_token_limit=per_run,
            tokens_used_run=await self.run_tokens_used(),
            price_in_per_1k=self.settings.LLM_PRICE_IN_PER_1K,
            price_out_per_1k=self.settings.LLM_PRICE_OUT_PER_1K,
        )

    # ------------------------------------------------------------------
    # the check
    # ------------------------------------------------------------------
    async def ensure_can_spend(
        self, estimated_tokens: int, *, max_tokens_out: int = 0
    ) -> None:
        """Raise :class:`BudgetExceeded` when the next call must not happen.

        ``estimated_tokens`` is the estimated prompt size. ``max_tokens_out``
        is the response cap; when non-zero the worst-case cost is reserved
        before the call so a single large completion cannot overshoot the
        monthly budget.
        """
        estimated_tokens = max(0, int(estimated_tokens))
        monthly, per_run = await self._org_limits()

        used = await self.run_tokens_used()
        if per_run > 0 and used + estimated_tokens > per_run:
            raise BudgetExceeded(
                "tokens",
                f"Batas token per run ({per_run}) akan terlampaui: "
                f"terpakai {used} + perkiraan {estimated_tokens}.",
            )

        spent = await self.spent_this_month()
        if monthly > 0 and spent >= monthly:
            raise BudgetExceeded(
                "usd",
                f"Anggaran bulanan organisasi (USD {monthly:.2f}) sudah terpakai "
                f"(USD {spent:.4f}).",
            )
        if monthly > 0 and max_tokens_out > 0:
            projected = spent + estimate_call_usd(
                estimated_tokens, max_tokens_out, self.settings
            )
            if projected > monthly:
                raise BudgetExceeded(
                    "usd",
                    f"Perkiraan biaya panggilan ini (USD {projected:.4f}) melewati "
                    f"anggaran bulanan (USD {monthly:.2f}).",
                )

    async def ensure_concurrency_allowed(self) -> None:
        """Enforce ``AGENT_MAX_CONCURRENT_PER_ORG`` (PRD A0 bound)."""
        from app.db.models import AgentRun
        from dataloom_contracts._base import AgentRunStatus

        limit = self.settings.AGENT_MAX_CONCURRENT_PER_ORG
        if limit <= 0:
            return
        active = (
            await self.session.execute(
                select(func.count(AgentRun.id)).where(
                    AgentRun.org_id == self.org_id,
                    AgentRun.status.in_(
                        [
                            AgentRunStatus.PENDING.value,
                            AgentRunStatus.RUNNING.value,
                            AgentRunStatus.WAITING_APPROVAL.value,
                        ]
                    ),
                )
            )
        ).scalar()
        if int(active or 0) >= limit:
            raise BudgetExceeded(
                "concurrency",
                f"Batas {limit} run agent konkuren untuk organisasi ini tercapai.",
            )


__all__ = [
    "BudgetExceeded",
    "BudgetGuard",
    "BudgetState",
    "cost_usd",
    "estimate_call_usd",
]
