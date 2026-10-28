"""FR-002 RBAC: owner >= admin >= editor > viewer.

Acceptance criterion from the PRD: "Viewer tidak bisa membuat/mengubah
pipeline (403)". Every ``can_*`` helper in ``app.core.rbac`` is asserted for all
four roles, so any change to the rank table or a helper body breaks a test.
"""

from __future__ import annotations

import pytest

# NOTE: ``pytest.ini`` uses ``--strict-markers``, so every marker must be
# registered there. ``unit`` / ``integration`` / ``tenant`` / ``recovery`` /
# ``agents`` are declared in ``tests/pytest.ini``.

ROLES = ("viewer", "editor", "admin", "owner")


def _rbac():
    from api_app.core import rbac

    return rbac


# ---------------------------------------------------------------------------
# Rank ordering
# ---------------------------------------------------------------------------
def test_rank_is_strictly_increasing_viewer_editor_admin_owner() -> None:
    rbac = _rbac()
    ranks = [rbac.rank(role) for role in ROLES]

    assert ranks == sorted(ranks), f"Hierarki peran tidak monoton: {dict(zip(ROLES, ranks))}"
    assert len(set(ranks)) == 4, "Dua peran punya peringkat sama -> hierarki tidak tegas."
    assert rbac.rank("viewer") >= 1, "Peringkat viewer harus >= 1 (bukan 0 = tak dikenal)."


def test_unknown_role_ranks_below_every_real_role() -> None:
    rbac = _rbac()
    for bogus in ("", "superuser", "OWNER", "root", None):
        assert rbac.rank(bogus) == 0, f"Peran tak dikenal {bogus!r} mendapat peringkat > 0."
        assert rbac.has_at_least(bogus, _role("viewer")) is False, (
            f"Peran tak dikenal {bogus!r} dianggap memenuhi syarat viewer."
        )


def _role(name: str):
    from dataloom_contracts._base import Role

    return Role(name)


# ---------------------------------------------------------------------------
# can_mutate: the FR-002 acceptance criterion
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("role", "expected"),
    [("viewer", False), ("editor", True), ("admin", True), ("owner", True)],
)
def test_can_mutate_matrix(role: str, expected: bool) -> None:
    rbac = _rbac()
    assert rbac.can_mutate(role) is expected, (
        f"can_mutate({role!r}) != {expected}. "
        "Kriteria penerimaan FR-002: viewer TIDAK boleh memutasi apa pun."
    )


def test_viewer_is_denied_every_mutating_permission() -> None:
    rbac = _rbac()
    # This is the exact list of helpers the router dependencies use.
    denied = {
        "can_mutate": rbac.can_mutate("viewer"),
        "can_manage_members": rbac.can_manage_members("viewer"),
        "can_manage_keys": rbac.can_manage_keys("viewer"),
        "can_delete_project": rbac.can_delete_project("viewer"),
        "can_run_pipeline": rbac.can_run_pipeline("viewer"),
    }
    assert denied == {name: False for name in denied}, (
        f"Viewer mendapat izin: {[k for k, v in denied.items() if v]}"
    )


def test_editor_can_run_and_mutate_but_not_administer() -> None:
    rbac = _rbac()

    assert rbac.can_mutate("editor") is True
    assert rbac.can_run_pipeline("editor") is True
    # Editing data is not administering the org.
    assert rbac.can_manage_members("editor") is False, (
        "Editor dapat mengelola anggota - eskalasi hak akses (FR-002)."
    )
    assert rbac.can_manage_keys("editor") is False, (
        "Editor dapat mengelola API key - seharusnya hanya owner/admin."
    )
    assert rbac.can_delete_project("editor") is False, (
        "Editor dapat menghapus proyek - seharusnya hanya owner/admin."
    )


@pytest.mark.parametrize(
    "helper",
    ["can_manage_members", "can_manage_keys", "can_delete_project"],
)
def test_admin_level_helpers_grant_admin_and_owner_only(helper: str) -> None:
    rbac = _rbac()
    fn = getattr(rbac, helper)

    assert fn("viewer") is False
    assert fn("editor") is False
    assert fn("admin") is True, f"{helper}('admin') False -> admin tidak bisa menjalankan tugasnya."
    assert fn("owner") is True, f"{helper}('owner') False -> owner kehilangan hak (mustahil)."


def test_owner_is_at_least_everything_admin_is() -> None:
    rbac = _rbac()
    admin_perms = {
        "mutate": rbac.can_mutate("admin"),
        "members": rbac.can_manage_members("admin"),
        "keys": rbac.can_manage_keys("admin"),
        "delete_project": rbac.can_delete_project("admin"),
        "run": rbac.can_run_pipeline("admin"),
    }
    owner_perms = {
        "mutate": rbac.can_mutate("owner"),
        "members": rbac.can_manage_members("owner"),
        "keys": rbac.can_manage_keys("owner"),
        "delete_project": rbac.can_delete_project("owner"),
        "run": rbac.can_run_pipeline("owner"),
    }
    assert all(owner_perms[key] for key in owner_perms), (
        f"Owner kehilangan izin: {[k for k, v in owner_perms.items() if not v]}"
    )
    assert all(
        owner_perms[key] >= admin_perms[key] for key in admin_perms
    ), f"Hak owner < admin: owner={owner_perms} admin={admin_perms}"


def test_matrix_is_monotone_for_every_helper() -> None:
    """Raising the role must never *remove* a permission (no privilege cliff)."""
    rbac = _rbac()
    helpers = (
        rbac.can_mutate,
        rbac.can_manage_members,
        rbac.can_manage_keys,
        rbac.can_delete_project,
        rbac.can_run_pipeline,
    )
    for helper in helpers:
        results = [helper(role) for role in ROLES]  # viewer -> owner
        assert results == sorted(results), (
            f"{helper.__name__} tidak monoton terhadap hierarki peran: "
            f"{dict(zip(ROLES, results))}"
        )


# ---------------------------------------------------------------------------
# has_at_least: the primitive behind every helper
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("required", ROLES)
def test_has_at_least_truth_table(required: str) -> None:
    rbac = _rbac()
    rank_required = rbac.rank(required)
    for role in ROLES:
        expected = rbac.rank(role) >= rank_required
        assert rbac.has_at_least(role, _role(required)) is expected, (
            f"has_at_least({role!r}, {required!r}) != {expected}"
        )
