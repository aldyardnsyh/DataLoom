"""Leader election scheduler (FR-028): hanya satu replika yang eksekusi job.

Kontrak yang dibuktikan:
1. Dua scheduler bersamaan -> hanya satu yang jadi leader (SET NX).
2. Follower skip tick tanpa menyentuh DB / enqueue.
3. Redis mati -> fail-open: single-replika tetap jalan.

Memakai ``FakeRedis``/``FakeSession`` dari conftest (tanpa network).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch


async def test_hanya_satu_replika_jadi_leader(fake_redis, monkeypatch) -> None:
    """Dua scheduler bersamaan -> hanya satu yang eksekusi job."""
    from api_app.services import scheduler as sched

    monkeypatch.setattr(sched, "_leader_token", "token-A")
    with patch("api_app.core.ratelimit.get_redis", return_value=fake_redis):
        assert await sched._ensure_leader() is True

    # Replika B (token beda) harus ditolak — kunci sudah milik A
    monkeypatch.setattr(sched, "_leader_token", "token-B")
    with patch("api_app.core.ratelimit.get_redis", return_value=fake_redis):
        assert await sched._ensure_leader() is False

    # Replika A refresh (masih pemilik) tetap leader
    monkeypatch.setattr(sched, "_leader_token", "token-A")
    with patch("api_app.core.ratelimit.get_redis", return_value=fake_redis):
        assert await sched._ensure_leader() is True


async def test_follower_skip_tick_tanpa_enqueue(fake_redis, monkeypatch) -> None:
    """Follower: _tick return awal, enqueue_run tidak dipanggil, DB tak tersentuh."""
    from api_app.services import scheduler as sched

    fake_redis.kv[sched.LEADER_KEY] = "pemilik-lain"
    monkeypatch.setattr(sched, "_leader_token", "bukan-pemilik")
    with (
        patch("api_app.core.ratelimit.get_redis", return_value=fake_redis),
        patch("api_app.services.scheduler.enqueue_run", new=AsyncMock()) as enqueue,
        patch(
            "api_app.services.scheduler.get_session_factory",
            side_effect=AssertionError("follower tidak boleh menyentuh DB"),
        ),
    ):
        await sched._tick()
    enqueue.assert_not_called()


async def test_redis_mati_fail_open_single_replika_jalan(fake_redis) -> None:
    """Redis down -> tetap jalan (single-replika tidak mati)."""
    from api_app.services import scheduler as sched

    fake_redis.raise_on = {"get"}
    with patch("api_app.core.ratelimit.get_redis", return_value=fake_redis):
        assert await sched._ensure_leader() is True
