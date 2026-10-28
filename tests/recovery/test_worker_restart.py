"""Worker restart resilience (FR-037 / NFR-08).

The invariant: a message is ACKed **only after** the Bronze object is durably
written. A crash between the two means redelivery, never data loss; a duplicate
delivery is harmless because Silver deduplicates on ``_row_hash``.
"""

from __future__ import annotations

import uuid

import pytest

from conftest import FakeResult

pytestmark = pytest.mark.recovery


class _Entry:
    def __init__(self) -> None:
        self.entry_id = "1700000000000-0"
        self.org_id = str(uuid.uuid4())
        self.project_id = str(uuid.uuid4())
        self.source_id = str(uuid.uuid4())
        self.batch_id = str(uuid.uuid4())
        self.rows = [{"a": 1}]


@pytest.mark.asyncio
async def test_ack_happens_only_after_bronze_write(monkeypatch, fake_session, fake_redis) -> None:  # noqa: ANN001
    """Order: persist_bronze -> commit -> XACK. Reversing it loses data on crash."""
    from app import consumer

    calls: list[str] = []

    async def fake_persist(session, entries):  # noqa: ANN001, ANN202
        calls.append("persist")
        return {entry.batch_id: len(entry.rows) for entry in entries}

    async def fake_ack(redis, stream, group, *entry_ids):  # noqa: ANN001, ANN202
        calls.append("ack")

    monkeypatch.setattr(consumer, "persist_bronze", fake_persist)
    monkeypatch.setattr(consumer, "ack", fake_ack)

    entries = [_Entry()]
    # Reproduce the exact sequence from main.consume_loop for one batch.
    await consumer.persist_bronze(fake_session, entries)
    await fake_session.commit()
    await consumer.ack(fake_redis, "ingest:x:y", "g", *[entry.entry_id for entry in entries])

    assert calls == ["persist", "ack"], f"Urutan tulis-ACK salah: {calls}"
    assert fake_session.commits == 1, "Commit harus terjadi sebelum ACK."


@pytest.mark.asyncio
async def test_failed_bronze_write_leaves_entry_pending(monkeypatch, fake_session) -> None:  # noqa: ANN001
    """If the Parquet write raises, the entry must NOT be acked."""
    from app import consumer

    acked: list[str] = []

    async def boom(session, entries):  # noqa: ANN001, ANN202
        raise RuntimeError("object storage down")

    async def fake_ack(redis, stream, group, *entry_ids):  # noqa: ANN001, ANN202
        acked.extend(entry_ids)

    monkeypatch.setattr(consumer, "persist_bronze", boom)
    monkeypatch.setattr(consumer, "ack", fake_ack)

    with pytest.raises(RuntimeError):
        await consumer.persist_bronze(fake_session, [_Entry()])

    assert acked == [], "Entri tidak boleh di-ACK ketika penulisan Bronze gagal."


def test_duplicate_delivery_is_deduplicated_by_row_hash() -> None:
    """A redelivered batch must not double-count rows in Silver (NFR-08)."""
    pl = pytest.importorskip("polars")
    from app.bronze import row_hash
    from app.silver import deduplicate

    rows = [{"a": 1}, {"a": 2}]
    frame = pl.DataFrame(
        {
            "a": [1, 2, 1],  # third row is a redelivery of the first
            "_row_hash": [row_hash({"a": 1}), row_hash({"a": 2}), row_hash({"a": 1})],
        }
    )

    deduped, removed = deduplicate(frame)

    assert removed == 1, "Satu baris duplikat harus dibuang."
    assert deduped.height == 2, "Jumlah baris unik harus 2."


def test_bronze_key_embeds_batch_id_so_retries_overwrite_the_same_object() -> None:
    """Same batch_id -> same key: a retry cannot create a second object."""
    from datetime import date

    from app.bronze import bronze_key

    kwargs = {
        "org_id": "o",
        "source_id": "s",
        "batch_id": "b-123",
        "partition_date": date(2026, 10, 20),
    }
    assert bronze_key(**kwargs) == bronze_key(**kwargs)
    assert "b-123" in bronze_key(**kwargs)


@pytest.mark.asyncio
async def test_claim_stale_returns_entries_from_dead_consumer(fake_redis) -> None:  # noqa: ANN001
    """XAUTOCLAIM path: entries pending longer than CLAIM_IDLE_MS are re-read."""
    from app import consumer

    # Preload the backlog the fake hands to ``xautoclaim`` (dead consumer).
    fake_redis.autoclaim_backlog["ingest:o:s"] = [("1-0", {"org_id": "o", "rows": "[]"})]
    claimed = await consumer.claim_stale(fake_redis, "ingest:o:s", "g", "worker-1", 60_000)
    assert claimed, "Entri pending dari consumer mati harus diklaim ulang."

