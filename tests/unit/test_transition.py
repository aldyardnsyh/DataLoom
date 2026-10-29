"""The run state machine.

The legal transitions are fixed::

    draft -> validated -> queued -> running -> succeeded
                             |         |-> failed -> retrying (<= N) -> running
                             |         |-> failed (retry habis) -> needs_attention
                             |-> cancelled

``transition()`` is the single entry point; anything else is a bug. These tests
enumerate the *entire* 9x9 space and assert the table exactly.

The DB is a ``FakeSession``: ``transition`` only needs ``SELECT ... FOR UPDATE``
to return one row and then ``flush()``.
"""

from __future__ import annotations

import itertools
import uuid
from datetime import datetime, timedelta, timezone

import pytest

RUN_ID = uuid.UUID("c0000000-0000-4000-8000-000000000001")
ORG_ID = uuid.UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
PROJECT_ID = uuid.UUID("a0000000-0000-4000-8000-000000000001")

ALL_STATUSES = (
    "draft",
    "validated",
    "queued",
    "running",
    "succeeded",
    "failed",
    "retrying",
    "needs_attention",
    "cancelled",
)

#: Hand-written so the test cannot inherit a bug from the module it checks.
EXPECTED_EDGES: frozenset[tuple[str, str]] = frozenset(
    {
        ("draft", "validated"),
        ("draft", "cancelled"),
        ("validated", "queued"),
        ("validated", "cancelled"),
        ("queued", "running"),
        ("queued", "cancelled"),
        ("running", "succeeded"),
        ("running", "failed"),
        ("running", "cancelled"),
        ("failed", "retrying"),
        ("failed", "needs_attention"),
        ("retrying", "running"),
        ("retrying", "needs_attention"),
        ("retrying", "cancelled"),
        ("needs_attention", "queued"),
        ("needs_attention", "cancelled"),
    }
)


def _transition_module():
    from worker_app import transition

    return transition


def _run_status(name: str):
    from dataloom_contracts._base import RunStatus

    return RunStatus(name)


def _make_run(status: str):
    from worker_app.db import Run

    return Run(
        id=RUN_ID,
        org_id=ORG_ID,
        project_id=PROJECT_ID,
        pipeline_id=uuid.UUID("c0000000-0000-4000-8000-00000000000a"),
        pipeline_version_id=uuid.UUID("c0000000-0000-4000-8000-00000000000b"),
        version=1,
        trigger="manual",
        status=status,
        rows_in=0,
        rows_out=0,
        rows_quarantined=0,
        retry_total=0,
    )


# ---------------------------------------------------------------------------
# The table itself
# ---------------------------------------------------------------------------
def test_allowed_table_matches_the_specification_exactly() -> None:
    transition = _transition_module()

    assert transition.ALLOWED == EXPECTED_EDGES, (
        "Tabel transisi menyimpang dari spesifikasi.\n"
        f"  Hanya di kode : {sorted(transition.ALLOWED - EXPECTED_EDGES)}\n"
        f"  Hanya di spesifikasi: {sorted(EXPECTED_EDGES - transition.ALLOWED)}"
    )


def test_terminal_states_are_succeeded_and_cancelled_only() -> None:
    transition = _transition_module()

    assert transition.TERMINAL == {"succeeded", "cancelled"}, (
        f"Terminal states salah: {transition.TERMINAL}. "
        "'failed' BUKAN terminal (bisa retry / needs_attention)."
    )
    for status in transition.TERMINAL:
        assert transition.is_terminal(status) is True
    for status in ("failed", "needs_attention", "retrying", "running"):
        assert transition.is_terminal(status) is False, (
            f"'{status}' dianggap terminal: run akan berhenti sebelum retry."
        )


def test_no_transition_leaves_a_terminal_state() -> None:
    transition = _transition_module()
    for terminal in transition.TERMINAL:
        outgoing = {target for (begin, target) in transition.ALLOWED if begin == terminal}
        assert outgoing == set(), (
            f"Status terminal '{terminal}' punya transisi keluar: {outgoing}."
        )


def test_every_status_is_reachable_from_draft() -> None:
    """No dead state: every status must be reachable from the initial state."""
    transition = _transition_module()
    reachable = {"draft"}
    changed = True
    while changed:
        changed = False
        for begin, target in transition.ALLOWED:
            if begin in reachable and target not in reachable:
                reachable.add(target)
                changed = True

    assert reachable == set(ALL_STATUSES), (
        f"Status tak terjangkau dari 'draft': {set(ALL_STATUSES) - reachable}"
    )


# ---------------------------------------------------------------------------
# Legal transitions really apply
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(("begin", "target"), sorted(EXPECTED_EDGES))
async def test_legal_transition_is_applied_and_returns_previous_status(
    begin: str, target: str, fake_session
) -> None:
    transition = _transition_module()
    run = _make_run(begin)
    fake_session.queue_scalar(run)

    previous = await transition.transition(fake_session, RUN_ID, _run_status(target))

    assert previous == begin, f"transisi mengembalikan '{previous}', harusnya '{begin}'."
    assert run.status == target, f"status run tetap '{run.status}', harusnya '{target}'."
    assert fake_session.flushes >= 1, "Transisi tidak memanggil flush(): UI tidak melihat perubahan."


@pytest.mark.parametrize(
    ("begin", "target"),
    [
        (begin, target)
        for begin, target in itertools.product(ALL_STATUSES, ALL_STATUSES)
        if begin != target and (begin, target) not in EXPECTED_EDGES
    ],
)
async def test_every_illegal_transition_raises_transition_error(
    begin: str, target: str, fake_session
) -> None:
    transition = _transition_module()
    run = _make_run(begin)
    fake_session.queue_scalar(run)

    with pytest.raises(transition.TransitionError) as excinfo:
        await transition.transition(fake_session, RUN_ID, _run_status(target))

    assert run.status == begin, (
        f"Transisi ilegal '{begin}'->'{target}' MENGUBAH status walau melempar galat."
    )
    assert begin in str(excinfo.value) and target in str(excinfo.value), (
        f"Pesan galat tidak menyebut kedua status: {excinfo.value}"
    )


# ---------------------------------------------------------------------------
# Side effects of specific transitions
# ---------------------------------------------------------------------------
async def test_running_sets_started_at_once_and_records_current_node(fake_session) -> None:
    transition = _transition_module()
    run = _make_run("queued")
    fake_session.queue_scalar(run)

    await transition.transition(fake_session, RUN_ID, _run_status("running"), node_id="n_source")

    assert run.started_at is not None, "started_at tidak diisi saat masuk 'running'."
    assert run.current_node_id == "n_source"

    first_started = run.started_at
    run.status = "retrying"
    fake_session.queue_scalar(run)
    await transition.transition(fake_session, RUN_ID, _run_status("running"), node_id="n_clean")

    assert run.started_at == first_started, (
        "started_at ditimpa pada percobaan kedua: durasi total run jadi salah."
    )


@pytest.mark.parametrize("terminal", ["succeeded", "cancelled"])
async def test_terminal_state_records_finished_at_and_duration(terminal: str, fake_session) -> None:
    transition = _transition_module()
    run = _make_run("running" if terminal == "succeeded" else "queued")
    run.started_at = run.started_at or datetime.now(tz=timezone.utc) - timedelta(seconds=2)
    fake_session.queue_scalar(run)

    await transition.transition(fake_session, RUN_ID, _run_status(terminal))

    assert run.finished_at is not None, f"Status terminal '{terminal}' tidak mencatat finished_at."
    assert run.duration_ms is not None and run.duration_ms >= 2000, (
        f"duration_ms={run.duration_ms} tidak mencerminkan durasi nyata."
    )


async def test_failed_and_needs_attention_also_record_finished_at(fake_session) -> None:
    transition = _transition_module()
    run = _make_run("running")
    run.started_at = datetime.now(tz=timezone.utc)
    fake_session.queue_scalar(run)

    await transition.transition(
        fake_session,
        RUN_ID,
        _run_status("failed"),
        error_code="node_failed",
        error_message="ValueError: node gagal (detail di log server)",
    )

    assert run.status == "failed"
    assert run.finished_at is not None, "Status 'failed' tidak mencatat finished_at."
    assert run.error_code == "node_failed"
    assert "ValueError" in (run.error_message or "")


async def test_error_message_is_truncated_to_the_column_width(fake_session) -> None:
    transition = _transition_module()
    run = _make_run("running")
    run.started_at = datetime.now(tz=timezone.utc)
    fake_session.queue_scalar(run)

    await transition.transition(
        fake_session, RUN_ID, _run_status("failed"), error_message="x" * 5000
    )

    assert run.error_message is not None and len(run.error_message) <= 2000, (
        f"error_message panjang {len(run.error_message or '')} > 2000 (kolom Text, UI)."
    )


async def test_missing_run_raises_transition_error_not_attribute_error(fake_session) -> None:
    transition = _transition_module()
    fake_session.queue_scalar(None)

    with pytest.raises(transition.TransitionError):
        await transition.transition(fake_session, RUN_ID, _run_status("running"))


async def test_retry_exhaustion_path_failed_to_needs_attention(fake_session) -> None:
    """'Setelah retry habis -> status needs_attention'."""
    transition = _transition_module()
    run = _make_run("failed")
    run.started_at = datetime.now(tz=timezone.utc)
    fake_session.queue_scalar(run)

    await transition.transition(fake_session, RUN_ID, _run_status("needs_attention"))

    assert run.status == "needs_attention"
    assert run.finished_at is not None


async def test_needs_attention_can_be_requeued_after_a_human_fix(fake_session) -> None:
    transition = _transition_module()
    run = _make_run("needs_attention")
    fake_session.queue_scalar(run)

    previous = await transition.transition(fake_session, RUN_ID, _run_status("queued"))

    assert previous == "needs_attention"
    assert run.status == "queued"
