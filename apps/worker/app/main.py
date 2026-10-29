"""Worker entrypoint: consume ingest streams and execute queued runs.

Two cooperating loops in one process (modular monolith, ADR-001):
1. ``consume_loop`` - Redis Streams -> Bronze (at-least-once, ack after write).
2. ``run_loop``     - queued ``runs`` -> DAG execution -> status transitions.

Both are cancellation-safe so ``docker stop`` leaves no half-acked batch.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal
import uuid
from collections.abc import AsyncIterator

import redis.asyncio as aioredis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app import consumer, metrics
from app.config import get_settings
from app.dag_runner import execute_run
from app.db import Run

logger = logging.getLogger("dataloom.worker")

_STOP = asyncio.Event()


async def session_factory() -> tuple[async_sessionmaker[AsyncSession], object]:
    settings = get_settings()
    engine = create_async_engine(settings.DATABASE_URL, pool_pre_ping=True, pool_size=5)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    return factory, engine


async def consume_loop(redis: aioredis.Redis, factory: async_sessionmaker[AsyncSession]) -> None:
    """Poll every known stream, claim stale entries, write Bronze, then ack."""
    settings = get_settings()
    while not _STOP.is_set():
        try:
            streams = await consumer.list_streams(redis)
            if not streams:
                await asyncio.sleep(1)
                continue

            for stream in streams:
                await consumer.ensure_group(redis, stream, settings.CONSUMER_GROUP)

                claimed = await consumer.claim_stale(
                    redis, stream, settings.CONSUMER_GROUP, settings.CONSUMER_NAME, settings.CLAIM_IDLE_MS
                )
                fresh = await consumer.read_new(
                    redis,
                    stream,
                    settings.CONSUMER_GROUP,
                    settings.CONSUMER_NAME,
                    settings.READ_COUNT,
                    settings.READ_BLOCK_MS,
                )
                entries_raw = claimed + fresh
                if not entries_raw:
                    continue

                decoded = [
                    e for e in (consumer.decode_entry(eid, fields) for eid, fields in entries_raw) if e
                ]
                acked_ids = [eid for (eid, _f), d in zip(entries_raw, decoded, strict=False) if d is None] if decoded else [eid for eid, _ in entries_raw]

                if decoded:
                    async with factory() as session:
                        try:
                            await consumer.persist_bronze(session, decoded)
                            # COMMIT FIRST, then ACK: a crash between the two
                            # results in a redelivery, never a lost message.
                            await session.commit()
                            await consumer.ack(
                                redis, stream, settings.CONSUMER_GROUP, *[e.entry_id for e in decoded]
                            )
                            metrics.batches_total.labels(result="bronze_written").inc()
                        except Exception:  # noqa: BLE001
                            await session.rollback()
                            logger.exception("Gagal menulis batch bronze; entri tetap pending")
                if acked_ids:
                    await consumer.ack(redis, stream, settings.CONSUMER_GROUP, *acked_ids)

                with contextlib.suppress(Exception):
                    metrics.stream_lag.labels(stream=stream).set(
                        int(await redis.xlen(stream))
                    )
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - the loop must outlive transient errors
            logger.exception("Kesalahan loop consumer; ulang dalam 2 detik")
            await asyncio.sleep(2)


async def run_loop(factory: async_sessionmaker[AsyncSession]) -> None:
    """Claim one queued run, release the lock, then execute it.

    The claim MUST commit before ``execute_run`` opens its own session. Holding
    the ``FOR UPDATE`` lock while a second session tries to lock the same row
    deadlocks the worker against itself.
    """
    from app.transition import TransitionError, transition
    from dataloom_contracts._base import RunStatus

    while not _STOP.is_set():
        try:
            run_id: uuid.UUID | None = None
            async with factory() as session:
                run = (
                    await session.execute(
                        select(Run)
                        .where(Run.status == "queued")
                        .order_by(Run.queued_at.asc().nulls_last(), Run.created_at.asc())
                        .limit(1)
                        .with_for_update(skip_locked=True)
                    )
                ).scalar_one_or_none()

                if run is not None:
                    run_id = run.id
                    try:
                        await transition(session, run_id, RunStatus.RUNNING)
                    except TransitionError:
                        run_id = None
                # Commit (or roll back) the claim so the row lock is released
                # before execute_run locks it again in a fresh session.
                await session.commit()

            if run_id is None:
                await asyncio.sleep(1)
                continue

            logger.info("Menjalankan run %s", run_id)
            async with factory() as run_session:
                status = await execute_run(run_session, run_id)
            logger.info("Run %s selesai dengan status %s", run_id, status)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.exception("Kesalahan loop run; ulang dalam 2 detik")
            await asyncio.sleep(2)


async def main() -> None:
    logging.basicConfig(level=get_settings().LOG_LEVEL)
    settings = get_settings()
    logger.info("DataLoom worker mulai consumer=%s group=%s", settings.CONSUMER_NAME, settings.CONSUMER_GROUP)

    factory, engine = await session_factory()
    redis = aioredis.from_url(settings.REDIS_URL, decode_responses=True)

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, _STOP.set)

    tasks = [
        asyncio.create_task(consume_loop(redis, factory)),
        asyncio.create_task(run_loop(factory)),
    ]
    try:
        await asyncio.gather(*tasks)
    finally:
        for task in tasks:
            task.cancel()
        await redis.aclose()
        await engine.dispose()
        logger.info("DataLoom worker berhenti")


if __name__ == "__main__":
    asyncio.run(main())
