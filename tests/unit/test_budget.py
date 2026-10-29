"""Budget guard (FR-035) â€” pure logic with the shared ``FakeSession``.

``BudgetGuard`` reads:
- the org row (monthly budget + per-run token limit) via ``execute(...).first()``
- spend via a ``SUM(cost_usd)`` scalar
- per-run token usage via a ``SUM(tokens_in + tokens_out)`` scalar

``FakeSession.queue`` supplies each result in call order, which is exactly the
order the guard issues them.
"""

from __future__ import annotations

import uuid

import pytest

from app.config import AgentSettings
from app.llm.budget import BudgetExceeded, BudgetGuard, cost_usd, estimate_call_usd

pytestmark = pytest.mark.unit


def _settings(**overrides: object) -> AgentSettings:
    """Deterministic settings; prices are set so cost maths is testable."""
    base = {
        "LLM_PRICE_IN_PER_1K": 0.5,
        "LLM_PRICE_OUT_PER_1K": 1.5,
        "DEFAULT_PER_RUN_TOKEN_LIMIT": 1000,
        "DEFAULT_LLM_BUDGET_USD_MONTH": 10.0,
    }
    base.update(overrides)
    return AgentSettings(**base)


def test_cost_usd_uses_configured_price_sheet() -> None:
    cost = cost_usd(tokens_in=2000, tokens_out=1000, settings=_settings())
    # 2 * 0.5 + 1 * 1.5 = 2.5
    assert cost == pytest.approx(2.5), "Biaya harus 2.5 USD untuk 2k/1k token."


def test_cost_usd_is_zero_when_prices_unset() -> None:
    """Zero means UNKNOWN (UV-25), never 'free'."""
    cost = cost_usd(
        tokens_in=10_000,
        tokens_out=10_000,
        settings=_settings(LLM_PRICE_IN_PER_1K=0.0, LLM_PRICE_OUT_PER_1K=0.0),
    )
    assert cost == 0.0, "Harga belum dikonfigurasi harus menghasilkan 0.0 (unknown)."


def test_estimate_call_usd_is_worst_case() -> None:
    estimate = estimate_call_usd(1000, 2000, settings=_settings())
    assert estimate == pytest.approx(0.5 + 3.0), "Estimasi harus memakai batas keluaran maksimum."


@pytest.mark.asyncio
async def test_per_run_token_limit_blocks_the_next_call(fake_session) -> None:  # noqa: ANN001
    """A run that already consumed its token budget must not start another call.

    Result order inside ``ensure_can_spend``: org limits, monthly spend,
    per-run usage.
    """
    guard = BudgetGuard(
        fake_session,
        org_id=uuid.uuid4(),
        agent_run_id=uuid.uuid4(),
        settings=_settings(DEFAULT_PER_RUN_TOKEN_LIMIT=100),
    )
    fake_session.queue_rows((10.0, 100))  # monthly budget, per-run limit
    fake_session.queue_scalar(95)  # tokens already used by this run
    fake_session.queue_scalar(0.0)  # spent this month

    with pytest.raises(BudgetExceeded) as excinfo:
        await guard.ensure_can_spend(estimated_tokens=50, max_tokens_out=50)
    assert excinfo.value.kind == "tokens", (
        f"Jenis pelanggaran harus 'tokens', bukan {excinfo.value.kind!r}."
    )


@pytest.mark.asyncio
async def test_monthly_budget_blocks_the_next_call(fake_session) -> None:  # noqa: ANN001
    guard = BudgetGuard(
        fake_session,
        org_id=uuid.uuid4(),
        agent_run_id=None,
        settings=_settings(DEFAULT_LLM_BUDGET_USD_MONTH=1.0),
    )
    fake_session.queue_rows((1.0, 20_000))  # budget already spent-ish
    fake_session.queue_scalar(1.5)  # over budget
    fake_session.queue_scalar(0)

    with pytest.raises(BudgetExceeded) as excinfo:
        await guard.ensure_can_spend(estimated_tokens=50, max_tokens_out=50)
    assert excinfo.value.kind == "usd", (
        f"Jenis pelanggaran harus 'usd', bukan {excinfo.value.kind!r}."
    )


@pytest.mark.asyncio
async def test_missing_org_row_fails_closed(fake_session) -> None:  # noqa: ANN001
    """An unknown org is a bug, not permission to spend money."""
    guard = BudgetGuard(fake_session, org_id=uuid.uuid4(), agent_run_id=None, settings=_settings())
    fake_session.queue_rows()  # no org row

    with pytest.raises(BudgetExceeded):
        await guard.ensure_can_spend(estimated_tokens=1, max_tokens_out=1)


@pytest.mark.asyncio
async def test_call_allowed_within_budget(fake_session) -> None:  # noqa: ANN001
    """Positive control: the guard must NOT raise when everything fits."""
    guard = BudgetGuard(
        fake_session,
        org_id=uuid.uuid4(),
        agent_run_id=uuid.uuid4(),
        settings=_settings(),
    )
    fake_session.queue_rows((10.0, 20_000))
    fake_session.queue_scalar(0.0)
    fake_session.queue_scalar(0)

    await guard.ensure_can_spend(estimated_tokens=10, max_tokens_out=10)


