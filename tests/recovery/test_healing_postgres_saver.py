"""Healing interrupt-resume vs PostgresSaver asli (skip bila tidak ada PG).

Lokal:  CHECKPOINT_DATABASE_URL=postgresql://dataloom:dataloom@localhost:55434/dataloom
Staging: sesuaikan host/kredensial; test ini jalan otomatis bila PG reachable.
"""
from __future__ import annotations

import os
import uuid

import pytest
from typing_extensions import TypedDict

pytestmark = pytest.mark.recovery
pytest.importorskip("langgraph", reason="langgraph wajib untuk uji interrupt")

DSN = os.environ.get(
    "CHECKPOINT_DATABASE_URL",
    "postgresql://dataloom:dataloom@localhost:55434/dataloom",
)


def _pg_reachable(dsn: str) -> bool:
    try:
        import psycopg
        with psycopg.connect(dsn, connect_timeout=3) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
        return True
    except Exception:
        return False


needs_pg = pytest.mark.skipif(
    not _pg_reachable(DSN), reason=f"Postgres tidak reachable di {DSN} — skip, bukan fail"
)


class _S(TypedDict):
    val: str


@needs_pg
def test_healing_interrupt_resume_postgres_saver() -> None:
    """Start sampai interrupt (WAITING_APPROVAL) lalu resume -> SUCCEEDED."""
    from langgraph.graph import END, START, StateGraph
    from langgraph.types import Command, interrupt
    from langgraph.checkpoint.postgres import PostgresSaver

    def n_approval(s: _S) -> _S:
        d = interrupt({"kind": "healing_fix_approval"})
        return {"val": "approved:" + str(d.get("approved_proposal_ids"))}

    def n_fin(s: _S) -> _S:
        return {"val": s["val"] + "|done"}

    g = StateGraph(_S)
    g.add_node("approval", n_approval)
    g.add_node("fin", n_fin)
    g.add_edge(START, "approval")
    g.add_edge("approval", "fin")
    g.add_edge("fin", END)

    with PostgresSaver.from_conn_string(DSN) as saver:
        saver.setup()
        graph = g.compile(checkpointer=saver)
        tid = "qa-pg-" + uuid.uuid4().hex[:8]
        cfg = {"configurable": {"thread_id": tid}}
        r1 = graph.invoke({"val": "start"}, config=cfg)
        assert "__interrupt__" in r1, "Graph harus berhenti di interrupt (WAITING_APPROVAL)"
        assert graph.get_state(cfg).next == ("approval",)
        r2 = graph.invoke(Command(resume={"approved_proposal_ids": ["p1"]}), config=cfg)
        assert r2["val"] == "approved:['p1']|done", r2
