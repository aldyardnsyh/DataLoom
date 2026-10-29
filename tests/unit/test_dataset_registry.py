"""Dataset registry: upsert is org/project/layer scoped and keeps real columns."""

from __future__ import annotations

import uuid

import polars as pl


def test_safe_dataset_name_is_a_bare_identifier() -> None:
    from app.datasets import safe_dataset_name

    assert safe_dataset_name("Quarantine Orders / 2026") == "quarantine_orders_2026"
    assert safe_dataset_name("  ") == "dataset"
    assert len(safe_dataset_name("x" * 200)) == 128


async def test_register_dataset_inserts_a_scoped_row(fake_session) -> None:  # noqa: ANN001
    from app.datasets import register_dataset
    from app.db import Dataset

    org_id = uuid.uuid4()
    project_id = uuid.uuid4()
    frame = pl.DataFrame({"order_id": ["A", "B"], "amount": [10, 20]})

    row = await register_dataset(
        fake_session,
        org_id=org_id,
        project_id=project_id,
        layer="silver",
        name="Silver Orders",
        storage_uri="s3://bucket/org/x/silver/orders.parquet",
        row_count=frame.height,
        frame=frame,
    )

    assert isinstance(row, Dataset)
    assert row.name == "silver_orders"
    assert row.org_id == org_id
    assert row.project_id == project_id
    assert row.layer == "silver"
    assert row.row_count == 2
    assert row.schema_json["columns"] == [
        {"name": "order_id", "dtype": "String"},
        {"name": "amount", "dtype": "Int64"},
    ]
    assert fake_session.added == [row]
    assert fake_session.flushes == 1
    assert "org_id" in fake_session.raw_sql[0]
    assert "project_id" in fake_session.raw_sql[0]


async def test_register_dataset_updates_the_latest_snapshot(fake_session) -> None:  # noqa: ANN001
    from app.datasets import register_dataset
    from app.db import Dataset

    org_id = uuid.uuid4()
    project_id = uuid.uuid4()
    existing = Dataset(
        org_id=org_id,
        project_id=project_id,
        layer="quarantine",
        name="quarantine_orders",
        schema_json={"columns": [{"name": "old", "dtype": "String"}]},
        storage_uri="s3://bucket/org/x/quarantine/old.parquet",
        row_count=1,
    )
    fake_session.queue_scalar(existing)

    row = await register_dataset(
        fake_session,
        org_id=org_id,
        project_id=project_id,
        layer="quarantine",
        name="quarantine_orders",
        storage_uri="s3://bucket/org/x/quarantine/new.parquet",
        row_count=9,
        columns=[{"name": "_reason_code", "dtype": "String"}],
        extra={"reasons": {"missing_required_value": 9}},
    )

    assert row is existing
    assert row.storage_uri.endswith("new.parquet")
    assert row.row_count == 9
    assert row.schema_json["reasons"] == {"missing_required_value": 9}
    assert row.schema_json["columns"] == [{"name": "_reason_code", "dtype": "String"}]
    assert fake_session.added == []
    assert fake_session.flushes == 1
