"""Wiring tests for the self-healing graph (no DB, no LLM)."""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch

from langgraph.checkpoint.memory import MemorySaver

from app.graphs import self_healing
from app.runner import build_healing_graph


def _ok_state(**over):
    base = {"status": "ok", "issues": [], "fix": {"title": "f"}}
    base.update(over)
    return base


def test_build_healing_graph_compiles_with_all_nodes():
    with patch("app.runner.checkpointer", return_value=MemorySaver()):
        graph = build_healing_graph(MagicMock(), provider=MagicMock(), guard=MagicMock())
    nodes = set(graph.nodes.keys())
    assert {"collect_evidence", "diagnose", "validate_fix", "needs_human", "approval"} <= nodes


def test_after_diagnose_routing():
    assert self_healing.after_diagnose({"status": "insufficient_evidence"}) == "needs_human"
    assert self_healing.after_diagnose({"status": "ok"}) == "validate_fix"


def test_after_validate_fix_routing():
    assert self_healing.after_validate_fix(_ok_state()) == "approval"
    assert self_healing.after_validate_fix(_ok_state(issues=[{"msg": "bad"}])) == "needs_human"


def test_max_proposals_per_run_is_two():
    assert self_healing.MAX_PROPOSALS_PER_RUN == 2


@pytest.mark.asyncio
async def test_third_proposal_rejected():
    import uuid as _uuid

    from app.graphs.base import ContractViolation

    class _R:
        def __init__(self, v):
            self._v = v

        def scalar(self):
            return self._v

    class _S:
        async def execute(self, *a, **k):
            return _R(2)

        async def flush(self):
            return None

    state = {
        "org_id": str(_uuid.uuid4()),
        "project_id": str(_uuid.uuid4()),
        "agent_run_id": str(_uuid.uuid4()),
        "fix": {"title": "f3"},
    }
    with pytest.raises(ContractViolation, match="Batas 2 usulan"):
        await self_healing.persist_healing_proposal(_S(), state=state)
