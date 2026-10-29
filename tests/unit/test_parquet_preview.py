"""Parquet preview is bounded, org-scoped, and returns rows not fabricated data."""

from __future__ import annotations

import io
import uuid

import polars as pl
import pytest


def _parquet_bytes() -> bytes:
    buffer = io.BytesIO()
    pl.DataFrame(
        {
            "order_id": ["A", "B", "C"],
            "amount": [10, 20, 30],
        }
    ).write_parquet(buffer)
    return buffer.getvalue()


def test_preview_parquet_reads_rows_and_marks_truncation(monkeypatch) -> None:  # noqa: ANN001
    from app.services import query_service

    org_id = uuid.uuid4()
    data = _parquet_bytes()
    monkeypatch.setattr(query_service.storage, "key_from_uri", lambda uri: f"org/{org_id}/x.parquet")
    monkeypatch.setattr(
        query_service.storage,
        "head_object",
        lambda key: {"size": len(data), "content_type": "application/vnd.apache.parquet"},
    )
    monkeypatch.setattr(query_service.storage, "get_bytes", lambda key: data)

    result = query_service._preview_parquet_sync(
        uri=f"s3://bucket/org/{org_id}/x.parquet",
        org_id=org_id,
        limit=2,
    )

    assert result.columns == ["order_id", "amount"]
    assert result.rows == [["A", 10], ["B", 20]]
    assert result.truncated is True


def test_preview_parquet_rejects_another_org(monkeypatch) -> None:  # noqa: ANN001
    from app.services import query_service

    org_id = uuid.uuid4()
    monkeypatch.setattr(query_service.storage, "key_from_uri", lambda uri: "org/other/x.parquet")
    monkeypatch.setattr(query_service.storage, "get_bytes", lambda key: b"not used")

    with pytest.raises(ValueError, match="organisasi"):
        query_service._preview_parquet_sync(
            uri="s3://bucket/org/other/x.parquet",
            org_id=org_id,
            limit=2,
        )
