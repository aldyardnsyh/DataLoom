"""Pipeline versioning: edits append, never mutate (FR-024).

Also covers the DAG validator rejection path: an invalid DAG must produce 422
and must NOT create a version row.
"""

from __future__ import annotations

import uuid

import pytest

from conftest import FakeResult

pytestmark = pytest.mark.integration


def _valid_dag() -> dict:
    return {
        "schema_version": 1,
        "nodes": [
            {"id": "n_src", "type": "source", "params": {"kind": "source", "source_id": str(uuid.uuid4())}},
            {"id": "n_sink", "type": "sink", "params": {"kind": "sink", "layer": "gold", "dataset_name": "gold_x"}},
        ],
        "edges": [{"from": "n_src", "to": "n_sink"}],
        "metadata": {},
    }


def _cyclic_dag() -> dict:
    dag = _valid_dag()
    dag["edges"] = [
        {"from": "n_src", "to": "n_sink"},
        {"from": "n_sink", "to": "n_src"},
    ]
    return dag


def test_dag_validator_endpoint_accepts_valid_dag(http_client, app_factory, owner_a) -> None:  # noqa: ANN001
    app_factory.login_as(owner_a)
    response = http_client.post(
        "/v1/pipelines/validate",
        headers={**owner_a.csrf_headers, "Content-Type": "application/json"},
        json={"dag": _valid_dag()},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["valid"] is True
    assert body["topological_order"] == ["n_src", "n_sink"]


def test_dag_validator_endpoint_rejects_cycle(http_client, app_factory, owner_a) -> None:  # noqa: ANN001
    app_factory.login_as(owner_a)
    response = http_client.post(
        "/v1/pipelines/validate",
        headers={**owner_a.csrf_headers, "Content-Type": "application/json"},
        json={"dag": _cyclic_dag()},
    )
    assert response.status_code == 200, "Validator mengembalikan hasil, bukan melempar galat."
    body = response.json()
    assert body["valid"] is False, "DAG siklik harus ditolak (FR-020)."
    assert body["issues"], "Harus ada pesan kesalahan yang bisa ditampilkan di kanvas."


async def test_patch_with_invalid_dag_creates_no_version(fake_session) -> None:  # noqa: ANN001
    """At the service layer: ``replace_dag`` raises before inserting a version."""
    from api_app.core.errors import ValidationProblem
    from api_app.services.pipeline_service import replace_dag

    pipeline = type(
        "P",
        (),
        {
            "id": uuid.uuid4(),
            "org_id": uuid.uuid4(),
            "current_version_id": None,
            "project_id": uuid.uuid4(),
        },
    )()

    with pytest.raises(ValidationProblem):
        await replace_dag(
            fake_session,
            pipeline=pipeline,
            dag_payload=_cyclic_dag(),
            user_id=uuid.uuid4(),
            change_note="tes",
        )

    assert fake_session.added == [], "DAG tidak valid tidak boleh menulis versi baru."


async def test_new_version_does_not_touch_previous_one(fake_session) -> None:  # noqa: ANN001
    """``create_version`` inserts a new row and repoints ``current_version_id``."""
    from api_app.services.pipeline_service import create_version
    from dataloom_contracts._base import ActorKind
    from dataloom_contracts.pipelines import PipelineDAG

    pipeline = type(
        "P",
        (),
        {
            "id": uuid.uuid4(),
            "org_id": uuid.uuid4(),
            "project_id": uuid.uuid4(),
            "current_version_id": uuid.uuid4(),  # pretend v1 exists
        },
    )()
    previous_id = pipeline.current_version_id

    dag = PipelineDAG.model_validate(_valid_dag())
    fake_session.queue_scalar(1)  # MAX(version) = 1 -> next is 2

    version = await create_version(
        fake_session,
        pipeline=pipeline,
        dag=dag,
        created_by_kind=ActorKind.USER,
        created_by_user_id=uuid.uuid4(),
    )

    assert version.version == 2, "Versi baru harus 2."
    assert version.parent_version_id == previous_id, "Versi baru harus menunjuk induknya."
    assert pipeline.current_version_id == version.id, "Pipeline harus menunjuk versi terbaru."
    assert fake_session.added, "Versi baru harus benar-benar ditulis."
