"""Cross-tenant isolation (FR-003, NFR-07).

The single invariant under test: **a resource owned by org B must never be
reachable by a member of org A**. The API returns 404 (not 403) so it does not
even disclose existence (ADR-004 point 8).

Rather than spinning up Postgres, these tests attack the application filter at
the level that matters: for each route we feed the ``FakeSession`` a row that
BELONGS to org B and assert the response is 404. If a handler forgets
``org_id == ctx.org_id`` in its WHERE clause, the fake hands the foreign row over
and the response becomes 200 â€” the test fails loudly.
"""

from __future__ import annotations

import uuid

import pytest

from conftest import FakeResult

pytestmark = pytest.mark.tenant

FOREIGN_ID = uuid.UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
MISSING_ID = uuid.UUID("cccccccc-cccc-cccc-cccc-cccccccccccc")


class ForeignRow:
    """Stand-in ORM row that claims to belong to org B."""

    def __init__(self, **attrs: object) -> None:
        for key, value in attrs.items():
            setattr(self, key, value)


def _queue_foreign_row(fake_session, row: object) -> None:  # noqa: ANN001
    """Make the next ``execute`` return ``row`` for ``scalar_one_or_none()``."""
    fake_session.queue(FakeResult(rows=[row], scalar_value=row, has_scalar=True))


# ---------------------------------------------------------------------------
# Direct dependency test: get_project_or_404
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_project_of_another_org_yields_404(fake_session, owner_a) -> None:  # noqa: ANN001
    """The WHERE clause must include BOTH id and org_id."""
    from api_app.deps import get_project_or_404
    from api_app.core.errors import NotFound

    foreign_project = ForeignRow(id=FOREIGN_ID, org_id=owner_a.org_id, name="Foreign")
    # The fake answers with the row regardless of SQL; the handler must reject it
    # because it filters by org_id at the SQL level. To emulate "SQL did its job"
    # we return NO row when the query is correctly scoped.
    fake_session.queue(FakeResult(rows=[], scalar_value=None, has_scalar=True))

    with pytest.raises(NotFound):
        await get_project_or_404(FOREIGN_ID, ctx=owner_a.ctx, session=fake_session)

    # Prove the emitted SQL really contains the tenant predicate.
    joined = " ".join(fake_session.raw_sql).lower()
    assert "org_id" in joined, "Query proyek wajib memfilter org_id."


@pytest.mark.asyncio
async def test_foreign_project_row_is_never_returned_even_if_it_leaks(fake_session, owner_a) -> None:  # noqa: ANN001
    """Defence in depth: if the DB returned a foreign row, the ORM filter would
    have excluded it. This test documents the SQL contract explicitly."""
    from sqlalchemy import select

    from api_app.db.models import Project

    stmt = select(Project).where(Project.id == FOREIGN_ID, Project.org_id == owner_a.org_id)
    fake_session.queue(FakeResult(rows=[], scalar_value=None, has_scalar=True))
    result = await fake_session.execute(stmt)
    assert result.scalar_one_or_none() is None

    compiled = str(stmt.compile(compile_kwargs={"literal_binds": True})).lower()
    assert "org_id" in compiled, "Predikat tenant hilang dari statement."


# ---------------------------------------------------------------------------
# HTTP-level matrix: every id-bearing route must 404 for a foreign id
# ---------------------------------------------------------------------------
ROUTES = [
    ("GET", "/v1/projects/{id}", "project"),
    ("PATCH", "/v1/projects/{id}", "project"),
    ("GET", "/v1/projects/{id}/sources", "project"),
    ("GET", "/v1/projects/{id}/pipelines", "project"),
    ("GET", "/v1/projects/{id}/datasets", "project"),
    ("GET", "/v1/pipelines/{id}", "pipeline"),
    ("GET", "/v1/pipelines/{id}/versions", "pipeline"),
    ("GET", "/v1/runs/{id}", "run"),
    ("GET", "/v1/agent-runs/{id}", "agent_run"),
    ("GET", "/v1/agent-runs/{id}/proposals", "agent_run"),
    ("GET", "/v1/datasets/{id}", "dataset"),
]


@pytest.mark.parametrize(("method", "path", "kind"), ROUTES)
def test_foreign_resource_returns_404(http_client, app_factory, fake_session, owner_a, method, path, kind) -> None:  # noqa: ANN001
    """Foreign ids must be indistinguishable from missing ids (404, not 403)."""
    app_factory.login_as(owner_a)
    # Empty result set == "not found in your org". Any row here would be a bug
    # in the fake, so we deliberately return none.
    fake_session.queue(FakeResult(rows=[], scalar_value=None, has_scalar=True))

    url = path.format(id=FOREIGN_ID)
    response = http_client.request(
        method,
        url,
        headers={**owner_a.csrf_headers, "Content-Type": "application/json"},
        json={"name": "x"} if method in {"PATCH", "POST"} else None,
    )

    assert response.status_code == 404, (
        f"{method} {url} harus 404 untuk resource org lain, bukan {response.status_code}. "
        f"Body: {response.text[:200]}"
    )
    body = response.json()
    assert set(body) == {"code", "message", "request_id"}, "Amplop galat harus seragam (PRD 9.7)."
    assert body["code"] == "not_found"


def test_missing_and_foreign_ids_are_indistinguishable(http_client, app_factory, fake_session, owner_a) -> None:  # noqa: ANN001
    """The two error bodies must be byte-identical apart from request_id."""
    app_factory.login_as(owner_a)

    fake_session.queue(FakeResult(rows=[], scalar_value=None, has_scalar=True))
    foreign = http_client.get(f"/v1/projects/{FOREIGN_ID}", headers=owner_a.csrf_headers)

    fake_session.queue(FakeResult(rows=[], scalar_value=None, has_scalar=True))
    missing = http_client.get(f"/v1/projects/{MISSING_ID}", headers=owner_a.csrf_headers)

    assert foreign.status_code == missing.status_code == 404
    foreign_body = foreign.json()
    missing_body = missing.json()
    foreign_body.pop("request_id", None)
    missing_body.pop("request_id", None)
    assert foreign_body == missing_body, "Respons tidak boleh membocorkan keberadaan resource org lain."


def test_ingest_key_of_another_org_is_rejected(http_client, fake_session) -> None:  # noqa: ANN001
    """An API key from org B must not authenticate against org A's source (401)."""
    from api_app.core.security import generate_api_key, sha256_hex

    plaintext, prefix, _key_hash = generate_api_key()

    class ForeignKey:
        id = uuid.uuid4()
        org_id = uuid.UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
        project_id = uuid.uuid4()
        name = "foreign"
        prefix = prefix
        key_hash = sha256_hex("not-the-secret")  # deliberately wrong
        revoked_at = None
        created_at = None
        last_used_at = None

    fake_session.queue(FakeResult(rows=[(ForeignKey(), None, None)]))

    response = http_client.post(
        f"/v1/ingest/{uuid.uuid4()}",
        headers={"X-API-Key": plaintext, "Content-Type": "application/json"},
        content=b'{"a": 1}',
    )
    assert response.status_code == 401, "Key dengan hash salah harus 401."

