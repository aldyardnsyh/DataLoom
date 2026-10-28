"""RBAC role hierarchy and permission checks (FR-002)."""

from __future__ import annotations

from dataloom_contracts._base import Role

#: Higher number = more privilege. Owner is the only role that can delete an
#: organization or transfer ownership.
_RANK: dict[str, int] = {
    Role.VIEWER.value: 1,
    Role.EDITOR.value: 2,
    Role.ADMIN.value: 3,
    Role.OWNER.value: 4,
}


def rank(role: str) -> int:
    return _RANK.get(role, 0)


def has_at_least(role: str, required: Role) -> bool:
    """True when ``role`` is at least as privileged as ``required``."""
    return rank(role) >= _RANK[required.value]


def can_mutate(role: str) -> bool:
    """Viewer must never mutate (FR-002 acceptance criterion)."""
    return has_at_least(role, Role.EDITOR)


def can_manage_members(role: str) -> bool:
    return has_at_least(role, Role.ADMIN)


def can_manage_keys(role: str) -> bool:
    return has_at_least(role, Role.ADMIN)


def can_delete_project(role: str) -> bool:
    return has_at_least(role, Role.ADMIN)


def can_run_pipeline(role: str) -> bool:
    return has_at_least(role, Role.EDITOR)
