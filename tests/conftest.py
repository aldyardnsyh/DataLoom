"""Shared pytest fixtures for the DataLoom test suite.

Assertions are written to be falsifiable: each one fails when the behaviour it
describes is wrong.

Design constraints:
- No network, no real Postgres, no real Redis, no object storage, no LLM.
- Deterministic: no clocks, no RNG in assertions (ids are fixed UUIDs).
- Isolated: every fixture builds its own state; nothing is shared across tests.

Environment setup happens at import time, before any ``app.*`` module is
imported, so ``Settings()`` sees a deterministic configuration and the
in-process API-key cache never leaks between tests.
"""

from __future__ import annotations

import importlib
import importlib.abc
import importlib.util
import json
import os
import sys
import uuid
from collections.abc import AsyncIterator, Callable, Iterator
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# 0. Make the three Python apps importable as their own ``app`` packages.
# ---------------------------------------------------------------------------
# The suite is expected to run from ``apps/api`` (``pyproject.toml`` sets
# ``testpaths = ["../../tests"]``), but ``apps/worker`` and ``apps/agents`` also
# publish a top-level package literally named ``app``. Unqualified ``import
# app.*`` would therefore silently resolve to whichever path came first.
#
# Strategy: every app lives in its own pseudo-distribution directory with
# ``sitecustomize.py`` + ``gen_dataloom_app_importer('.')``. Importing
# ``<pkg>_app.<x>`` asks the installed ``dataloom_ai_coder`` module (workdir =
# ``path``) where ``app/<x>.py`` lives and loads exactly that file as
# ``app.<x>``.
_REPO_ROOT = Path(__file__).resolve().parents[1]

#: Test-only environment. ``setdefault`` so a developer can override locally.
_TEST_ENV: dict[str, str] = {
    "ENV": "dev",
    "LOG_LEVEL": "WARNING",
    # No real datastore is ever contacted: these URLs are never opened because
    # every DB/Redis dependency is overridden in ``app_factory``.
    "DATABASE_URL": "postgresql+asyncpg://test:test@127.0.0.1:1/dataloom_test",
    "REDIS_URL": "redis://127.0.0.1:1/0",
    "SECRET_KEY": "test-only-secret-key-not-used-anywhere-0123456789",
    "COOKIE_SECURE": "false",
    "TRUSTED_HOSTS": '["*"]',
    "SCHEDULER_ENABLED": "false",
    "AGENT_EXECUTION_ENABLED": "false",
    "S3_BUCKET": "dataloom-test",
    "DUCKDB_PATH": "/tmp/dataloom-test-duckdb",
}
for _key, _value in _TEST_ENV.items():
    os.environ.setdefault(_key, _value)

#: Insert every app root so ``<pkg>_app.<x>`` imports resolve.
_APP_ROOTS = {
    "api": _REPO_ROOT / "apps" / "api",
    "worker": _REPO_ROOT / "apps" / "worker",
    "agents": _REPO_ROOT / "apps" / "agents",
}
for _name, _root in _APP_ROOTS.items():
    _root_str = str(_root)
    if _root.exists() and _root_str not in sys.path:
        sys.path.insert(0, _root_str)

_CONTRACTS_ROOT = _REPO_ROOT / "packages" / "contracts"
if _CONTRACTS_ROOT.exists() and str(_CONTRACTS_ROOT) not in sys.path:
    sys.path.insert(0, str(_CONTRACTS_ROOT))

#: Tests import ``api_app`` / ``worker_app`` / ``agents_app`` because all three
#: applications publish a top-level package literally named ``app``. Only one
#: of them can own ``app`` at a time, so the autouse fixture below binds the
#: right root for the current test module and registers the alias.
_APP_BY_TEST = {
    "test_csrf": "api",
    "test_ingest_hot_path": "api",
    "test_ingest_limits": "api",
    "test_installation_settings": "api",
    "test_parquet_preview": "api",
    "test_pipeline_versions": "api",
    "test_rbac": "api",
    "test_security": "api",
    "test_tenant_isolation": "api",
    "test_agent_interrupt_resume": "agents",
    "test_budget": "agents",
    "test_profiler_contract": "agents",
    "test_prompts_injection": "agents",
    "test_sql_guard": "agents",
    "test_dataset_registry": "worker",
    "test_gate": "worker",
    "test_quarantine_reasons": "worker",
    "test_reconciliation": "worker",
    "test_silver": "worker",
    "test_transition": "worker",
    "test_worker_restart": "worker",
}


_BOUND_APP: str | None = None
_ALIAS_FINDER: "_AliasFinder | None" = None


class _AliasLoader(importlib.abc.Loader):
    def __init__(self, module: object) -> None:
        self.module = module

    def create_module(self, spec):  # noqa: ANN001, ANN201
        return self.module

    def exec_module(self, module) -> None:  # noqa: ANN001
        return None


class _AliasFinder(importlib.abc.MetaPathFinder):
    """Map ``api_app.x`` to the already-loaded ``app.x`` module object."""

    def __init__(self, alias: str) -> None:
        self.alias = alias

    def find_spec(self, fullname: str, path=None, target=None):  # noqa: ANN001, ANN201
        if not (fullname == self.alias or fullname.startswith(self.alias + ".")):
            return None
        target_name = "app" + fullname[len(self.alias) :]
        module = importlib.import_module(target_name)
        return importlib.util.spec_from_loader(fullname, _AliasLoader(module))


def _bind_app(name: str, *, force: bool = False) -> None:
    global _BOUND_APP, _ALIAS_FINDER
    if not force and _BOUND_APP == name and "app" in sys.modules:
        return
    if _ALIAS_FINDER is not None:
        try:
            sys.meta_path.remove(_ALIAS_FINDER)
        except ValueError:
            pass
        _ALIAS_FINDER = None
    root = _APP_ROOTS[name]
    for module_name in list(sys.modules):
        if module_name == "app" or module_name.startswith("app."):
            del sys.modules[module_name]
        if module_name == f"{name}_app" or module_name.startswith(f"{name}_app."):
            del sys.modules[module_name]
    for app_root in _APP_ROOTS.values():
        while str(app_root) in sys.path:
            sys.path.remove(str(app_root))
    sys.path.insert(0, str(root))
    module = importlib.import_module("app")
    sys.modules[f"{name}_app"] = module
    _ALIAS_FINDER = _AliasFinder(f"{name}_app")
    sys.meta_path.insert(0, _ALIAS_FINDER)
    _BOUND_APP = name


@pytest.fixture(autouse=True)
def bind_test_app(request: pytest.FixtureRequest) -> Iterator[None]:
    """Expose the app under test as ``<name>_app`` for this module."""
    stem = Path(request.module.__file__).stem if request.module is not None else ""
    app_name = _APP_BY_TEST.get(stem)
    if app_name:
        _bind_app(app_name)
    yield

requires_api = pytest.mark.skipif(
    not (_APP_ROOTS["api"] / "app" / "main.py").exists(),
    reason="apps/api tidak tersedia di checkout ini.",
)
requires_worker = pytest.mark.skipif(
    not (_APP_ROOTS["worker"] / "app" / "silver.py").exists(),
    reason="apps/worker tidak tersedia di checkout ini.",
)
requires_agents = pytest.mark.skipif(
    not (_APP_ROOTS["agents"] / "app" / "graphs" / "profiler.py").exists(),
    reason="apps/agents tidak tersedia di checkout ini.",
)


# ---------------------------------------------------------------------------
# 1. Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture(scope="session")
def anyio_backend() -> str:
    """``anyio`` runs every async test on asyncio only (no trio dependency)."""
    return "asyncio"


@pytest.fixture()
def console_trace() -> list[str]:
    """Ordered log used by tests that must prove call *sequencing* (XACK last).

    A plain list is used instead of a Mock because the order of side effects is
    the property under test (FR-021/NFR-08), not the call count.
    """
    return []


@pytest.fixture()
def org_a() -> uuid.UUID:
    """Deterministic tenant A id (fixed so failures are reproducible)."""
    return uuid.UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")


@pytest.fixture()
def org_b() -> uuid.UUID:
    """Deterministic tenant B id (the "foreign org" of every isolation test)."""
    return uuid.UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")


@pytest.fixture()
def project_a(org_a: uuid.UUID) -> uuid.UUID:
    return uuid.UUID("a0000000-0000-4000-8000-000000000001")


@pytest.fixture()
def project_b(org_b: uuid.UUID) -> uuid.UUID:
    return uuid.UUID("b0000000-0000-4000-8000-000000000001")


# ---- 1a. Fake SQLAlchemy session -----------------------------------------
@dataclass(slots=True)
class FakeResult:
    """Minimal stand-in for ``sqlalchemy.engine.Result``.

    ``rows`` is a list of tuples (``first()``/``all()``) or a list of ORM
    objects (``scalars().all()``). ``scalar_one_or_none()`` returns
    ``scalar_value`` when it was supplied explicitly.
    """

    rows: list[Any] = field(default_factory=list)
    scalar_value: Any = None
    has_scalar: bool = False

    def first(self) -> Any:
        return self.rows[0] if self.rows else None

    def all(self) -> list[Any]:
        return list(self.rows)

    def scalars(self) -> "FakeScalars":
        return FakeScalars(self.rows)

    def scalar_one_or_none(self) -> Any:
        if self.has_scalar:
            return self.scalar_value
        return self.rows[0] if self.rows else None

    def scalar(self) -> Any:
        if self.has_scalar:
            return self.scalar_value
        return self.rows[0] if self.rows else None


class FakeScalars:
    """``Result.scalars()`` proxy supporting ``.all()`` / ``.first()``."""

    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def all(self) -> list[Any]:
        return list(self._rows)

    def first(self) -> Any:
        return self._rows[0] if self._rows else None


class FakeSession:
    """In-memory ``AsyncSession`` double.

    What it records (so a test can *prove* a write happened):
    - ``added``: ORM objects passed to ``session.add``
    - ``executed``: SQLAlchemy statements passed to ``execute`` (in order)
    - ``flushes`` / ``commits`` / ``rollbacks``: counters
    - ``raw_sql``: statements compiled with the ``postgresql`` dialect, so a
      test can assert the ``WHERE`` clause really contains ``org_id``.

    ``queue(obj)`` makes the next ``execute`` return ``obj``; ``scalar_next``
    queued values win for both ``.scalar_one_or_none()`` and ``.scalar()``.

    It is deliberately dumb: no magic, no autoflush, no identity map. A test
    that needs a query result says so explicitly.
    """

    default_scalar: Any = None

    def __init__(self) -> None:
        self.added: list[Any] = []
        self.executed: list[Any] = []
        self.raw_sql: list[str] = []
        self.flushes = 0
        self.commits = 0
        self.rollbacks = 0
        self.refreshed: list[Any] = []
        self.deleted: list[Any] = []
        self._results: list[Any] = []
        self._scalars: list[Any] = []
        self._in_transaction = False
        self.info: dict[str, Any] = {}

    # ---- queueing ------------------------------------------------------
    def queue(self, result: Any) -> None:
        """Next ``execute`` resolves to ``result``."""
        self._results.append(result)

    def queue_rows(self, *rows: Any) -> None:
        self._results.append(FakeResult(rows=list(rows)))

    def queue_scalar(self, value: Any) -> None:
        """Next ``execute`` resolves to a Result whose scalar is ``value``."""
        self._results.append(FakeResult(rows=[value], scalar_value=value, has_scalar=True))

    def queue_values(self, *values: Any) -> None:
        """Next ``execute`` resolves to a Result returning these scalars/rows."""
        self._scalars.extend(values)

    # ---- AsyncSession surface -----------------------------------------
    async def execute(self, statement: Any, *args: Any, **kwargs: Any) -> FakeResult:
        self.executed.append(statement)
        self._capture_sql(statement)
        if self._results:
            return self._results.pop(0)
        if self._scalars:
            value = self._scalars.pop(0)
            return FakeResult(rows=[value], scalar_value=value, has_scalar=True)
        return FakeResult(rows=[], scalar_value=self.default_scalar)

    def _capture_sql(self, statement: Any) -> None:
        """Best-effort textual SQL so tenant tests can grep for ``org_id``."""
        try:
            from sqlalchemy.dialects import postgresql

            compiled = statement.compile(
                dialect=postgresql.dialect(),
                compile_kwargs={"literal_binds": True},
            )
            self.raw_sql.append(str(compiled))
            return
        except Exception:  # noqa: BLE001 - non-SQL statements (e.g. text())
            pass
        try:
            self.raw_sql.append(str(statement))
        except Exception:  # noqa: BLE001 - exotic objects: ignore
            self.raw_sql.append("<unprintable>")

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    def add_all(self, objs: Any) -> None:
        self.added.extend(objs)

    async def flush(self) -> None:
        self.flushes += 1

    async def commit(self) -> None:
        self.commits += 1
        self._in_transaction = False

    async def rollback(self) -> None:
        self.rollbacks += 1
        self._in_transaction = False

    async def refresh(self, obj: Any, *args: Any, **kwargs: Any) -> None:
        self.refreshed.append(obj)

    async def delete(self, obj: Any) -> None:
        self.deleted.append(obj)

    async def close(self) -> None:
        return None

    async def __aenter__(self) -> "FakeSession":
        return self

    async def __aexit__(self, *exc_info: Any) -> bool:
        return False

    # ---- transaction helpers used by ``app.db.rls.set_tenant_context`` ---
    def in_transaction(self) -> bool:
        return self._in_transaction

    async def begin(self) -> None:
        self._in_transaction = True


@pytest.fixture()
def fake_session() -> FakeSession:
    """One isolated ``FakeSession`` per test."""
    return FakeSession()


@pytest.fixture()
def fake_session_factory() -> Callable[[], FakeSession]:
    """Factory so a test can build several sessions (worker loops do this)."""

    def _make() -> FakeSession:
        return FakeSession()

    return _make


# ---- 1b. Fake Redis -------------------------------------------------------
class FakePipeline:
    """``redis.asyncio`` pipeline double: queues commands, ``execute`` drains."""

    def __init__(self, redis: "FakeRedis") -> None:
        self.redis = redis
        self._queued: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    def xadd(self, key: str, fields: dict[str, Any], **kwargs: Any) -> "FakePipeline":
        self._queued.append(("xadd", (key, fields), kwargs))
        return self

    def xack(self, key: str, group: str, *ids: str) -> "FakePipeline":
        self._queued.append(("xack", (key, group, *ids), {}))
        return self

    def set(self, key: str, value: Any, *args: Any, **kwargs: Any) -> "FakePipeline":
        self._queued.append(("set", (key, value), {}))
        return self

    def delete(self, *keys: str) -> "FakePipeline":
        self._queued.append(("delete", keys, {}))
        return self

    async def execute(self) -> list[Any]:
        out: list[Any] = []
        queued, self._queued = self._queued, []
        for name, args, kwargs in queued:
            method = getattr(self.redis, name)
            result = method(*args, **kwargs)
            if hasattr(result, "__await__"):
                result = await result
            out.append(result)
        return out

    async def __aenter__(self) -> "FakePipeline":
        return self

    async def __aexit__(self, *exc_info: Any) -> bool:
        return False


class FakeRedis:
    """Deterministic in-memory stand-in for ``redis.asyncio.Redis``.

    Implements exactly the surface the product uses:
    ``xadd/xlen/xreadgroup/xack/xautoclaim/xgroup_create/pipeline/get/set/eval``
    (plus ``ping/scan/delete`` for completeness).

    Deterministic ids: ``<ms>-<seq>`` with a monotonic counter, so a test can
    assert on entry ids without a clock.
    """

    def __init__(self) -> None:
        self.streams: dict[str, list[tuple[str, dict[str, str]]]] = {}
        self.acked: dict[tuple[str, str], list[str]] = {}
        self.kv: dict[str, Any] = {}
        self.groups: dict[tuple[str, str], list[tuple[str, dict[str, str]]]] = {}
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self._seq = 0
        #: ``eval`` result override: None -> 1 (allowed), else the value returned.
        self.eval_result: Any = 1
        self.raise_on: set[str] = set()
        #: Entries handed back once by ``xautoclaim`` (crash recovery scenario).
        self.autoclaim_backlog: dict[str, list[tuple[str, dict[str, str]]]] = {}

    # ---- helpers -------------------------------------------------------
    def _next_id(self) -> str:
        self._seq += 1
        return f"{1700000000000 + self._seq}-0"

    def _record(self, name: str, *args: Any) -> None:
        self.calls.append((name, args))

    def _maybe_raise(self, name: str) -> None:
        if name in self.raise_on:
            raise ConnectionError(f"Redis tidak tersedia (simulasi pada {name}).")

    def xlen_sync(self, key: str) -> int:
        return len(self.streams.get(key, []))

    def seed(self, key: str, rows: int) -> None:
        """Pre-fill a stream with ``rows`` entries (stream length assertions)."""
        bucket = self.streams.setdefault(key, [])
        for index in range(rows):
            bucket.append(
                (
                    self._next_id(),
                    {
                        "org_id": "seeded",
                        "source_id": "seeded",
                        "project_id": "seeded",
                        "batch_id": f"seed-{index}",
                        "rows": "[]",
                    },
                )
            )

    # ---- Redis API -----------------------------------------------------
    async def xadd(self, key: str, fields: dict[str, Any], **kwargs: Any) -> str:
        self._record("xadd", key, fields)
        self._maybe_raise("xadd")
        entry_id = self._next_id()
        normalised = {str(k): str(v) for k, v in fields.items()}
        self.streams.setdefault(key, []).append((entry_id, normalised))
        return entry_id

    async def xlen(self, key: str) -> int:
        self._record("xlen", key)
        self._maybe_raise("xlen")
        return self.xlen_sync(key)

    async def xreadgroup(
        self,
        group: str,
        consumer: str,
        streams: dict[str, str],
        count: int | None = None,
        block: int | None = None,
    ) -> list[tuple[str, list[tuple[str, dict[str, str]]]]]:
        self._record("xreadgroup", group, consumer, streams)
        self._maybe_raise("xreadgroup")
        out: list[tuple[str, list[tuple[str, dict[str, str]]]]] = []
        for stream, _cursor in streams.items():
            cursor_key = (stream, group)
            consumed = self.groups.setdefault(cursor_key, [])
            available = self.streams.get(stream, [])
            fresh = [entry for entry in available if entry not in consumed]
            if count is not None:
                fresh = fresh[: int(count)]
            for entry in fresh:
                consumed.append(entry)
            if fresh:
                out.append((stream, fresh))
        return out

    async def xack(self, key: str, group: str, *ids: str) -> int:
        self._record("xack", key, group, *ids)
        self._maybe_raise("xack")
        bucket = self.acked.setdefault((key, group), [])
        bucket.extend(ids)
        return len(ids)

    async def xautoclaim(
        self,
        key: str,
        group: str,
        consumer: str,
        min_idle_time: int = 0,
        start_id: str = "0-0",
        count: int | None = None,
    ) -> list[Any]:
        self._record("xautoclaim", key, group, consumer, min_idle_time)
        self._maybe_raise("xautoclaim")
        backlog = self.autoclaim_backlog.pop(key, [])
        if count is not None:
            backlog = backlog[: int(count)]
        return ["0-0", backlog, []]

    async def xgroup_create(self, key: str, group: str, **kwargs: Any) -> bool:
        self._record("xgroup_create", key, group)
        self._maybe_raise("xgroup_create")
        self.groups.setdefault((key, group), [])
        return True

    async def xpending(self, key: str, group: str) -> dict[str, int]:
        self._record("xpending", key, group)
        return {"pending": 0}

    async def xinfo_consumers(self, key: str, group: str) -> list[dict[str, Any]]:
        self._record("xinfo_consumers", key, group)
        return []

    async def get(self, key: str) -> Any:
        self._record("get", key)
        self._maybe_raise("get")
        return self.kv.get(key)

    async def set(self, key: str, value: Any, *args: Any, **kwargs: Any) -> bool:
        self._record("set", key, value)
        self._maybe_raise("set")
        self.kv[key] = value
        return True

    async def delete(self, *keys: str) -> int:
        self._record("delete", *keys)
        removed = 0
        for key in keys:
            removed += 1 if self.kv.pop(key, None) is not None else 0
        return removed

    async def eval(self, script: str, numkeys: int, *args: Any) -> Any:
        self._record("eval", script, numkeys, *args)
        self._maybe_raise("eval")
        return self.eval_result

    async def scan(self, cursor: int = 0, match: str | None = None, count: int = 10) -> tuple[int, list[str]]:
        self._record("scan", cursor, match, count)
        prefix = (match or "").rstrip("*")
        keys = [key for key in self.streams if key.startswith(prefix)]
        return 0, keys

    async def ping(self) -> bool:
        return True

    def pipeline(self, transaction: bool = True) -> FakePipeline:
        return FakePipeline(self)

    async def aclose(self) -> None:
        return None

    def calls_named(self, name: str) -> list[tuple[Any, ...]]:
        return [args for called, args in self.calls if called == name]


@pytest.fixture()
def fake_redis() -> FakeRedis:
    """One isolated in-memory Redis per test."""
    return FakeRedis()


# ---- 1c. AuthContext helpers ---------------------------------------------
@dataclass(slots=True)
class Actor:
    """Everything a test needs to act as one member of one org."""

    org_id: uuid.UUID
    user_id: uuid.UUID
    role: str
    email: str
    session_token: str
    csrf_token: str
    ctx: Any = None  # app.deps.AuthContext
    user: Any = None  # app.db.models.User
    session_row: Any = None  # app.db.models.Session

    @property
    def cookies(self) -> dict[str, str]:
        """Cookies a browser would send for this actor."""
        return {"dl_session": self.session_token, "dl_csrf": self.csrf_token}

    @property
    def csrf_headers(self) -> dict[str, str]:
        return {"X-CSRF-Token": self.csrf_token}


def _build_actor(role: str, org_id: uuid.UUID, *, suffix: str) -> Actor:
    """Construct ORM rows + ``AuthContext`` without touching a database."""
    from api_app.db.models import Membership, Organization, Session as SessionRow, User
    from api_app.core.security import new_csrf_token, new_session_token, sha256_hex
    from api_app.deps import AuthContext

    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    token = new_session_token()
    csrf = new_csrf_token()

    user = User(
        id=uuid.uuid5(uuid.NAMESPACE_URL, f"user:{org_id}:{suffix}"),
        email=f"{role}-{suffix}@example.test",
        password_hash="$argon2id$v=19$m=65536,t=3,p=4$placeholder$placeholder",
        name=f"{role.title()} {suffix}",
        is_active=True,
        created_at=now,
    )
    org = Organization(
        id=org_id,
        name=f"Org {suffix}",
        slug=f"org-{suffix}",
        plan="free",
        llm_budget_usd_month=5.0,
        per_run_token_limit=20_000,
        created_at=now,
        updated_at=now,
    )
    membership = Membership(
        id=uuid.uuid5(uuid.NAMESPACE_URL, f"membership:{org_id}:{suffix}"),
        org_id=org_id,
        user_id=user.id,
        role=role,
        created_at=now,
    )
    session_row = SessionRow(
        id=uuid.uuid5(uuid.NAMESPACE_URL, f"session:{org_id}:{suffix}"),
        org_id=org_id,
        user_id=user.id,
        token_hash=sha256_hex(token),
        csrf_token_hash=sha256_hex(csrf),
        expires_at=datetime.now(tz=timezone.utc) + timedelta(hours=1),
        revoked_at=None,
        created_at=now,
    )
    ctx = AuthContext(
        user=user,
        session=session_row,
        org_id=org_id,
        org_name=org.name,
        role=role,
    )
    return Actor(
        org_id=org_id,
        user_id=user.id,
        role=role,
        email=user.email,
        session_token=token,
        csrf_token=csrf,
        ctx=ctx,
        user=user,
        session_row=session_row,
    )


@pytest.fixture()
def make_auth():
    """``make_auth(role, org_id, suffix=...) -> Actor`` for any role/org."""

    def _factory(role: str, org_id: uuid.UUID, *, suffix: str = "a") -> Actor:
        return _build_actor(role, org_id, suffix=suffix)

    return _factory


@pytest.fixture()
def owner_a(make_auth: Any, org_a: uuid.UUID) -> Actor:
    return make_auth("owner", org_a, suffix="a")


@pytest.fixture()
def admin_a(make_auth: Any, org_a: uuid.UUID) -> Actor:
    return make_auth("admin", org_a, suffix="a")


@pytest.fixture()
def editor_a(make_auth: Any, org_a: uuid.UUID) -> Actor:
    return make_auth("editor", org_a, suffix="a")


@pytest.fixture()
def viewer_a(make_auth: Any, org_a: uuid.UUID) -> Actor:
    return make_auth("viewer", org_a, suffix="a")


@pytest.fixture()
def owner_b(make_auth: Any, org_b: uuid.UUID) -> Actor:
    """Owner of the *other* org: the attacker in every isolation test."""
    return make_auth("owner", org_b, suffix="b")


# ---- 1d. FastAPI app with dependency overrides ---------------------------
@pytest.fixture()
def api_app():
    """Build a real FastAPI app WITHOUT a lifespan (no Redis/DB/scheduler).

    ``create_app()`` is the production factory; calling it here (instead of
    import-time ``app``) means each test gets a private router tree. The
    lifespan is never started by the ASGI test transport below, so
    ``get_redis()`` is never invoked and no connection is attempted.
    """
    from api_app.main import create_app

    return create_app()


@pytest.fixture()
def app_factory(api_app: Any, fake_session: FakeSession, fake_redis: FakeRedis):
    """Return ``(app, client)`` with DB + Redis + tenant RLS all overridden.

    Overrides installed:
    - ``app.db.session.get_session``   -> the test's ``FakeSession``
    - ``app.deps.get_session``         -> the same object (delta generator)
    - ``app.db.rls.set_tenant_context``-> no-op (RLS is a no-op without Postgres;
      the ``tests/tenant`` suite proves the *application* filter, and
      ``tests/tenant/test_rls_contract.py`` proves the *migration*)
    - ``app.core.ratelimit.get_redis`` -> the in-memory ``FakeRedis``

    Returns a small object with ``app``, ``client`` (sync portal) and the
    ability to ``login_as(actor)`` which installs an auth dependency override.
    """
    from api_app.api.v1 import ingest as ingest_module
    from api_app.core import ratelimit as ratelimit_module
    from api_app.db import rls as rls_module
    from api_app.db import session as session_module
    from api_app import deps as deps_module

    async def _session_override() -> AsyncIterator[FakeSession]:
        yield fake_session

    async def _noop_tenant_context(session: Any, org_id: uuid.UUID) -> None:
        return None

    api_app.dependency_overrides[session_module.get_session] = _session_override
    api_app.dependency_overrides[deps_module.get_session] = _session_override
    api_app.dependency_overrides[rls_module.set_tenant_context] = _noop_tenant_context
    api_app.dependency_overrides[deps_module.set_tenant_context] = _noop_tenant_context

    # Redis: patch the module attribute so both `get_redis()` call sites
    # (ratelimit + ingest_service) see the fake.
    _original_get_redis = ratelimit_module.get_redis
    ratelimit_module.get_redis = lambda: fake_redis  # type: ignore[assignment]

    class _Harness:
        def __init__(self) -> None:
            self.app = api_app
            self.fake_session = fake_session
            self.fake_redis = fake_redis
            self._current_actor: Actor | None = None

        def login_as(self, actor: Actor) -> None:
            """Make every request act as ``actor`` (skips cookies/DB lookups)."""
            self._current_actor = actor

            async def _ctx_override() -> Any:
                return actor.ctx

            api_app.dependency_overrides[deps_module.get_org_context] = _ctx_override

        def reset(self) -> None:
            for dependency in (
                deps_module.get_org_context,
                deps_module.require_mutation,
            ):
                api_app.dependency_overrides.pop(dependency, None)
            self._current_actor = None

    harness = _Harness()
    try:
        yield harness
    finally:
        ratelimit_module.get_redis = _original_get_redis  # type: ignore[assignment]
        api_app.dependency_overrides.clear()


@pytest.fixture()
def http_client(app_factory: Any):
    """Sync HTTP client bound to the overridden app (no lifespan, no sockets).

    ``raise_server_exceptions=False`` because several tests assert on the
    uniform ``{code,message,request_id}`` error envelope produced by the
    app-level exception handlers, including the 500 handler (SEC-25).
    """
    from fastapi.testclient import TestClient

    client = TestClient(app_factory.app, raise_server_exceptions=False)
    try:
        yield client
    finally:
        client.close()


# ---- 1e. Misc helpers -----------------------------------------------------
@pytest.fixture()
def ingestion_key() -> tuple[str, str, str]:
    """A syntactically valid API key: ``(plaintext, prefix, sha256(secret))``."""
    from api_app.core.security import generate_api_key

    return generate_api_key()


@pytest.fixture()
def fixed_now() -> datetime:
    """A frozen UTC instant, so any timestamp assertion is deterministic."""
    return datetime(2026, 10, 20, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture()
def json_body() -> Callable[[Any], bytes]:
    """Encode a payload exactly like an ingest producer would."""

    def _encode(payload: Any) -> bytes:
        return json.dumps(payload, separators=(",", ":")).encode("utf-8")

    return _encode


@pytest.fixture()
def dumps() -> Callable[[Any], str]:
    """Deterministic JSON for error-message assertions."""

    def _dumps(payload: Any) -> str:
        return json.dumps(payload, sort_keys=True, ensure_ascii=False)

    return _dumps


def make_mock(**attrs: Any) -> MagicMock:
    """``MagicMock`` with explicit attributes (avoids auto-attribute surprises)."""
    mock = MagicMock()
    for key, value in attrs.items():
        setattr(mock, key, value)
    return mock


# Bind a default app so test modules that import ``app.*`` at collection time
# (before the autouse fixture runs) resolve to a real package.
_bind_app("agents")


__all__ = [
    "Actor",
    "FakeRedis",
    "FakeResult",
    "FakeScalars",
    "FakeSession",
    "console_trace",
    "make_mock",
    "requires_agents",
    "requires_api",
    "requires_worker",
]

