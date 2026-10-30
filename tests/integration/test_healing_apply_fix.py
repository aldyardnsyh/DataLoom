"""Healing apply-fix: approved fix -> new version + requeue (FR-024, FR-037).

- valid fix -> version N+1 created, run requeued, audit written
- invalid dag -> 422, no version written
- cross-org agent_run -> 404 (FR-003)
- editor/viewer -> 403 (owner/admin only)
"""

from __future__ import annotations

import uuid

import pytest

from conftest import FakeResult

pytestmark = pytest.mark.integration


def _valid_dag(source_id: str) -> dict:
    return {
        "schema_version": 1,
        "nodes": [
            {"id": "n_src", "type": "source", "params": {"kind": "source", "source_id": source_id}},
            {"id": "n_sink", "type": "sink", "params": {"kind": "sink", "layer": "gold", "dataset_name": "gold_x"}},
        ],
        "edges": [{"from": "n_src", "to": "n_sink"}],
        "metadata": {},
    }


def _cyclic_dag(source_id: str) -> dict:
    dag = _valid_dag(source_id)
    dag["edges"] = [{"from": "n_src", "to": "n_sink"}, {"from": "n_sink", "to": "n_src"}]
    return dag


def _row(**kw):
    obj = type("R", (), {})()
    for k, v in kw.items():
        setattr(obj, k, v)
    return obj


def _queue_service_state(fake_session, *, org_id, agent_run_id, pipeline_id, dag, requeue_ok=True):
    """Queue FakeSession results in the order healing_service queries them."""
    agent_run = _row(id=agent_run_id, org_id=org_id, target_run_id=None)
    proposal = _row(
        id=uuid.uuid4(), agent_run_id=agent_run_id, org_id=org_id,
        kind="fix_proposal", status="approved",
        payload_json={"pipeline_id": str(pipeline_id), "dag": dag},
        applied_pipeline_version_id=None,
    )
    pipeline = _row(
        id=pipeline_id, org_id=org_id, project_id=uuid.uuid4(),
        current_version_id=uuid.uuid4(),
    )
    failed_run = _row(id=uuid.uuid4(), pipeline_id=pipeline_id, org_id=org_id)
    # 1 agent_run, 2 proposal, 3 pipeline (get_pipeline_scoped),
    # 4 max(version) for next_version_number, 5 project (enqueue), 6 active runs
    fake_session.queue(FakeResult(rows=[agent_run]))
    fake_session.queue(FakeResult(rows=[proposal]))
    fake_session.queue(FakeResult(rows=[pipeline]))
    fake_session.queue_scalar(0)  # next version number base
    # enqueue_run re-queries: pipeline, version (valid), project, active runs
    fake_session.queue(FakeResult(rows=[pipeline]))
    fake_session.queue(FakeResult(rows=[_row(id=uuid.uuid4(), version=1, validation_status="valid")]))
    fake_session.queue(FakeResult(rows=[_row(id=uuid.uuid4(), org_id=org_id, settings_json={})]))
    fake_session.queue(FakeResult(rows=[]))  # no active runs
    return agent_run, proposal, pipeline


async def test_valid_fix_creates_version_and_requeues(fake_session, org_a) -> None:
    from api_app.services.healing_service import apply_approved_healing_fix

    agent_run_id = uuid.uuid4()
    pipeline_id = uuid.uuid4()
    dag = _valid_dag(str(uuid.uuid4()))
    agent_run, proposal, pipeline = _queue_service_state(
        fake_session, org_id=org_a, agent_run_id=agent_run_id,
        pipeline_id=pipeline_id, dag=dag,
    )
    result = await apply_approved_healing_fix(
        fake_session, org_id=org_a, agent_run_id=agent_run_id,
        user_id=uuid.uuid4(), requeue=True,
    )
    assert result.pipeline_id == pipeline_id
    assert result.version == 1
    assert result.requeued_run_id is not None
    kinds = [type(o).__name__ for o in fake_session.added]
    assert "PipelineVersion" in kinds
    assert "Run" in kinds
    assert "AuditLog" in kinds
    # New version only: old current_version untouched except repoint.
    assert proposal.applied_pipeline_version_id is not None


async def test_invalid_dag_raises_422_no_version(fake_session, org_a) -> None:
    from api_app.core.errors import ValidationProblem
    from api_app.services.healing_service import apply_approved_healing_fix

    agent_run_id = uuid.uuid4()
    pipeline_id = uuid.uuid4()
    dag = _cyclic_dag(str(uuid.uuid4()))
    agent_run = _row(id=agent_run_id, org_id=org_a, target_run_id=None)
    proposal = _row(
        id=uuid.uuid4(), agent_run_id=agent_run_id, org_id=org_a,
        kind="fix_proposal", status="approved",
        payload_json={"pipeline_id": str(pipeline_id), "dag": dag},
        applied_pipeline_version_id=None,
    )
    pipeline = _row(id=pipeline_id, org_id=org_a, project_id=uuid.uuid4(),
                    current_version_id=uuid.uuid4())
    fake_session.queue(FakeResult(rows=[agent_run]))
    fake_session.queue(FakeResult(rows=[proposal]))
    fake_session.queue(FakeResult(rows=[pipeline]))
    with pytest.raises(ValidationProblem):
        await apply_approved_healing_fix(
            fake_session, org_id=org_a, agent_run_id=agent_run_id,
            user_id=uuid.uuid4(), requeue=False,
        )
    assert fake_session.added == []


async def test_cross_org_agent_run_404(fake_session, org_a, org_b) -> None:
    from api_app.core.errors import NotFound
    from api_app.services.healing_service import apply_approved_healing_fix

    fake_session.queue(FakeResult(rows=[]))  # agent_run not in org_b
    with pytest.raises(NotFound):
        await apply_approved_healing_fix(
            fake_session, org_id=org_b, agent_run_id=uuid.uuid4(),
            user_id=uuid.uuid4(), requeue=False,
        )


async def test_no_approved_proposal_409(fake_session, org_a) -> None:
    from api_app.core.errors import Conflict
    from api_app.services.healing_service import apply_approved_healing_fix

    agent_run = _row(id=uuid.uuid4(), org_id=org_a, target_run_id=None)
    fake_session.queue(FakeResult(rows=[agent_run]))
    fake_session.queue(FakeResult(rows=[]))  # no approved proposal
    with pytest.raises(Conflict):
        await apply_approved_healing_fix(
            fake_session, org_id=org_a, agent_run_id=agent_run.id,
            user_id=uuid.uuid4(), requeue=False,
        )


def test_endpoint_guards_require_admin_role() -> None:
    """Static guard: endpoint memakai require_role(ADMIN) + tenant org_id."""
    from pathlib import Path

    src = Path(r"D:\Riset Pribadi\DataLoom\apps\api\app\api\v1\agents.py").read_text()
    seg = src[src.index("apply-fix"):]
    seg = seg[:seg.index("/agent-runs/{agent_run_id}/apply\"")]
    assert "require_role(Role.ADMIN)" in seg
    assert "org_id=ctx.org_id" in seg
