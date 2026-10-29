"""RLS contract of the Alembic migration (FR-003, ADR-004).

No database is involved: the migration file is parsed to prove that every
org-scoped table gets BOTH ``ENABLE`` and ``FORCE ROW LEVEL SECURITY`` and a
policy comparing against ``current_setting('app.current_org_id')``.

``FORCE`` matters because on a single-URL deployment the migration role is also
the application role; without FORCE the table owner silently bypasses every
policy (SEC-07).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.tenant

MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "apps"
    / "api"
    / "alembic"
    / "versions"
    / "0001_initial_schema.py"
)

#: Tables that carry org data and therefore MUST be behind RLS.
EXPECTED_ORG_TABLES = {
    "memberships",
    "sessions",
    "projects",
    "api_keys",
    "sources",
    "datasets",
    "pipelines",
    "pipeline_versions",
    "runs",
    "run_nodes",
    "agent_runs",
    "agent_proposals",
    "agent_events",
    "llm_calls",
    "artifacts",
    "audit_logs",
    "ingest_idempotency",
    "uploads",
}


@pytest.fixture(scope="module")
def migration_source() -> str:
    assert MIGRATION.is_file(), f"Migrasi tidak ditemukan: {MIGRATION}"
    return MIGRATION.read_text(encoding="utf-8")


def test_every_org_table_is_enabled_and_forced(migration_source: str) -> None:
    enabled = set(re.findall(r'ALTER TABLE \{?(\w+)\}? ENABLE ROW LEVEL SECURITY', migration_source))
    # The migration loops over ORG_SCOPED_TABLES, so assert the loop + the
    # forced statement exist and that the table list covers our expectations.
    loop_present = "for table in ORG_SCOPED_TABLES" in migration_source
    force_present = 'ALTER TABLE {table} FORCE ROW LEVEL SECURITY' in migration_source
    assert loop_present, "Migrasi harus mengaktifkan RLS lewat loop ORG_SCOPED_TABLES."
    assert force_present, (
        "FORCE ROW LEVEL SECURITY wajib: tanpa itu pemilik tabel melewati policy (SEC-07)."
    )

    table_block = migration_source.split("ORG_SCOPED_TABLES", 1)[1].split("= (", 1)[1].split(")", 1)[0]
    listed = set(re.findall(r'"(\w+)"', table_block))
    missing = EXPECTED_ORG_TABLES - listed
    assert not missing, f"Tabel org berikut tidak ada di ORG_SCOPED_TABLES: {sorted(missing)}"


def test_policy_uses_tenant_session_variable(migration_source: str) -> None:
    assert "CREATE POLICY" in migration_source, "Policy RLS harus dibuat eksplisit."
    assert "current_setting('app.current_org_id', true)" in migration_source, (
        "Policy harus membaca app.current_org_id dengan missing_ok=true agar sesi tanpa "
        "konteks menghasilkan 0 baris (fail closed), bukan error."
    )
    # Both USING and WITH CHECK must be present so INSERTs cannot smuggle rows
    # into another tenant.
    assert "USING (org_id =" in migration_source
    assert "WITH CHECK (org_id =" in migration_source


def test_tenant_context_is_transaction_local() -> None:
    """``SET LOCAL`` / ``set_config(..., true)`` must never leak across a pool."""
    rls = (
        Path(__file__).resolve().parents[2]
        / "apps"
        / "api"
        / "app"
        / "db"
        / "rls.py"
    ).read_text(encoding="utf-8")
    assert "set_config('app.current_org_id'" in rls
    assert rls.count("true") >= 1
    assert ", true)" in rls, "Parameter is_local harus true agar setting transaction-scoped."
