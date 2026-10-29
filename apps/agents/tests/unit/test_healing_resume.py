"""Healing resume routing + apply_healing_fix tests."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest

from app import runner
from dataloom_contracts._base import AgentType


class _Run:
    def __init__(self, agent_type: str):
        self.id = uuid.uuid4()
        self.org_id = uuid.uuid4()
        self.agent_type = agent_type
        self.thread_id = "t"
        self.status = "waiting_approval"
        self.input_json = {}
        self.finished_at = None


class _Session:
    def __init__(self, run=None, proposal=None):
        self._run = run
        self._proposal = proposal
        self.committed = False

    async def execute(self, *a, **k):
        class R:
            def __init__(self, v):
                self._v = v

            def scalar_one_or_none(self):
                return self._v

        # Distinguish proposal query (has limit) is hard; return proposal
        # only when explicitly set, else run.
        return R(self._proposal if getattr(self, "_want_proposal", False) else self._run)

    async def flush(self):
        return None

    async def commit(self):
        self.committed = True


class _Scope:
    def __init__(self, session):
        self._s = session

    async def __aenter__(self):
        return self._s

    async def __aexit__(self, *a):
        return False


class _Graph:
    def __init__(self):
        self.ainvoke = AsyncMock()


def _patch(agent_type: str, proposal=None):
    run = _Run(agent_type)
    session = _Session(run=run, proposal=proposal)
    graph = _Graph()
    return run, session, graph


@pytest.mark.asyncio
async def test_resume_routing_healing():
    run, session, graph = _patch(AgentType.SELF_HEALING.value)
    with (
        patch.object(runner, "session_scope", return_value=_Scope(session)),
        patch.object(runner, "build_healing_graph", return_value=graph) as uoi_heal,
        patch.object(runner, "build_profiler_graph") as uoi_prof,
        patch.object(runner, "build_provider") as prov,
        patch.object(runner, "BudgetGuard", lambda *a, **k: object()),
        patch.object(runner, "get_settings") as gs,
    ):
        gs.return_value.AGENT_EXECUTION_ENABLED = True
        prov.return_value.aclose = AsyncMock()
        out = await runner.resume_agent(thread_id="t", approved_proposal_ids=["x"], org_id=run.org_id)
    uoi_heal.assert_called_once()
    uoi_prof.assert_not_called()
    assert out.status == "succeeded"
    assert run.finished_at is not None


@pytest.mark.asyncio
async def test_resume_routing_profiler():
    run, session, graph = _patch(AgentType.SCHEMA_PROFILER.value)
    with (
        patch.object(runner, "session_scope", return_value=_Scope(session)),
        patch.object(runner, "build_healing_graph") as uoi_heal,
        patch.object(runner, "build_profiler_graph", return_value=graph) as uoi_prof,
        patch.object(runner, "build_provider") as prov,
        patch.object(runner, "BudgetGuard", lambda *a, **k: object()),
        patch.object(runner, "get_settings") as gs,
    ):
        gs.return_value.AGENT_EXECUTION_ENABLED = True
        prov.return_value.aclose = AsyncMock()
        await runner.resume_agent(thread_id="t", approved_proposal_ids=[], org_id=run.org_id)
    uoi_prof.assert_called_once()
    uoi_heal.assert_not_called()


class _Proposal:
    def __init__(self, payload):
        self.payload_json = payload


@pytest.mark.asyncio
async def test_apply_rejects_invalid_dag():
    run_id, org_id = uuid.uuid4(), uuid.uuid4()
    session = _Session(proposal=_Proposal({"dag": {"nodes": []}}))
    session._want_proposal = True
    with patch.object(runner, "session_scope", return_value=_Scope(session)):
        with pytest.raises(Exception):
            await runner.apply_healing_fix(agent_run_id=run_id, org_id=org_id)


@pytest.mark.asyncio
async def test_apply_accepts_parameter_only():
    run_id, org_id = uuid.uuid4(), uuid.uuid4()
    session = _Session(proposal=_Proposal({"retry_backoff_s": 30}))
    session._want_proposal = True
    # Second execute() call loads the AgentRun; flip flag via side effect.
    calls = {"n": 0}
    orig_exec = session.execute

    async def exec2(*a, **k):
        calls["n"] += 1
        if calls["n"] >= 2:
            session._want_proposal = False
            session._run = _Run(AgentType.SELF_HEALING.value)
        return await orig_exec(*a, **k)

    session.execute = exec2  # type: ignore[method-assign]
    with patch.object(runner, "session_scope", return_value=_Scope(session)):
        plan = await runner.apply_healing_fix(agent_run_id=run_id, org_id=org_id)
    assert plan["new_version_needed"] is True
    assert plan["dag_fix"] is None
    assert plan["parameter_fix"] == {"retry_backoff_s": 30}
