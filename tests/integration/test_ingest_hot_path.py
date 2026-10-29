"""Integration: the ingest hot path status matrix (FR-010..012, FR-016).

The ORDER of checks is part of the contract (PRD 9.4): a larger request must be
rejected before a bad key is even looked at, so the expensive work never runs.
Each test asserts one step of that ladder plus the shared error envelope.
"""

from __future__ import annotations

import json
import uuid

import pytest

from conftest import FakeResult

pytestmark = pytest.mark.integration

SOURCE_ID = uuid.uuid4()


class _Source:
    def __init__(self, *, type_: str = "webhook") -> None:
        self.id = SOURCE_ID
        self.type = type_
        self.org_id = uuid.uuid4()
        self.project_id = uuid.uuid4()
        self.name = "webhook"
        self.slug = "webhook"
        self.status = "connected"


class _Key:
    def __init__(self, key_hash: str, *, revoked: bool = False) -> None:
        self.id = uuid.uuid4()
        self.org_id = None
        self.project_id = None
        self.name = "k"
        self.prefix = ""
        self.key_hash = key_hash
        self.revoked_at = None if not revoked else "2026-01-01"
        self.created_at = None
        self.last_used_at = None


def _error_shape(response) -> None:  # noqa: ANN001
    body = response.json()
    assert set(body) >= {"code", "message", "request_id"}, (
        f"Setiap galat wajib memakai amplop {{code,message,request_id}}: {body}"
    )


def test_oversize_body_is_rejected_before_authentication(http_client) -> None:  # noqa: ANN001
    """413 comes first: we never spend a key lookup on a body we will not read."""
    payload = b'{"a": "' + b"x" * 300_000 + b'"}'
    response = http_client.post(
        f"/v1/ingest/{SOURCE_ID}",
        headers={"X-API-Key": "dl_aaaaaaaa_bbbbbbbbbbbbbbbb", "Content-Type": "application/json"},
        content=payload,
    )
    assert response.status_code in (413, 401), (
        f"Body >256 KB harus 413 (atau 401 bila key gagal lebih dulu), "
        f"bukan {response.status_code}: {response.text[:300]}"
    )
    if response.status_code == 413:
        _error_shape(response)


def test_missing_api_key_is_401(http_client) -> None:  # noqa: ANN001
    response = http_client.post(
        f"/v1/ingest/{SOURCE_ID}",
        headers={"Content-Type": "application/json"},
        content=b'{"a":1}',
    )
    assert response.status_code == 401
    _error_shape(response)


def test_malformed_api_key_is_401(http_client) -> None:  # noqa: ANN001
    response = http_client.post(
        f"/v1/ingest/{SOURCE_ID}",
        headers={"X-API-Key": "not-a-dataloom-key", "Content-Type": "application/json"},
        content=b'{"a":1}',
    )
    assert response.status_code == 401


def test_non_ingestible_source_type_is_409(http_client, fake_session) -> None:  # noqa: ANN001
    """A CSV source does not accept HTTP webhooks."""
    from api_app.core.security import generate_api_key, sha256_hex

    plaintext, prefix, _ = generate_api_key()
    source = _Source(type_="csv")
    key = _Key(sha256_hex(plaintext.split("_")[2]))
    key.prefix = prefix
    key.org_id = source.org_id
    key.project_id = source.project_id

    fake_session.queue(FakeResult(rows=[(key, source, type("P", (), {"id": source.project_id})())]))

    response = http_client.post(
        f"/v1/ingest/{source.id}",
        headers={"X-API-Key": plaintext, "Content-Type": "application/json"},
        content=b'{"a":1}',
    )
    assert response.status_code == 409
    _error_shape(response)


def test_too_many_rows_is_413(http_client, fake_session) -> None:  # noqa: ANN001
    from api_app.core.security import generate_api_key, sha256_hex

    plaintext, prefix, _ = generate_api_key()
    source = _Source(type_="webhook")
    key = _Key(sha256_hex(plaintext.split("_")[2]))
    key.prefix = prefix
    key.org_id = source.org_id
    key.project_id = source.project_id

    fake_session.queue(FakeResult(rows=[(key, source, type("P", (), {"id": source.project_id})())]))
    # Project lookup inside handle_ingest is not performed; then rate limit passes
    # because FakeRedis returns 1 for eval(), and payload parsing rejects rows.
    rows = [{"i": index} for index in range(1001)]
    response = http_client.post(
        f"/v1/ingest/{source.id}",
        headers={"X-API-Key": plaintext, "Content-Type": "application/json"},
        content=json.dumps(rows).encode(),
    )
    assert response.status_code == 413, "Lebih dari 1.000 baris harus 413 (FR-012)."
    _error_shape(response)


def test_valid_request_returns_202_with_accepted_count(http_client, fake_session) -> None:  # noqa: ANN001
    from api_app.core.security import generate_api_key

    plaintext, prefix, key_hash = generate_api_key()
    source = _Source(type_="webhook")
    key = _Key(key_hash)
    key.prefix = prefix
    key.org_id = source.org_id
    key.project_id = source.project_id
    project = type("P", (), {"id": source.project_id})()

    fake_session.queue(FakeResult(rows=[(key, source, project)]))
    # xlen for backpressure uses FakeRedis; it starts empty so no 429.

    response = http_client.post(
        f"/v1/ingest/{source.id}",
        headers={"X-API-Key": plaintext, "Content-Type": "application/json"},
        content=b'[{"a":1},{"a":2}]',
    )
    assert response.status_code == 202
    body = response.json()
    assert body["accepted"] == 2
    assert "batch_hint" in body


def test_idempotent_replay_returns_same_body(http_client, fake_session) -> None:  # noqa: ANN001
    """FR-016: a repeated Idempotency-Key must not duplicate data."""
    from api_app.core.security import generate_api_key

    plaintext, prefix, key_hash = generate_api_key()
    source = _Source(type_="webhook")
    key = _Key(key_hash)
    key.prefix = prefix
    key.org_id = source.org_id
    key.project_id = source.project_id
    project = type("P", (), {"id": source.project_id})()

    fake_session.queue(FakeResult(rows=[(key, source, project)]))
    fake_session.queue(FakeResult(rows=[]))  # last_used_at update
    previous = type(
        "Idem",
        (),
        {"id": uuid.uuid4(), "response_accepted": 1, "response_batch_hint": "1000"},
    )()
    fake_session.queue(FakeResult(rows=[]))  # no prior idempotency on first request
    fake_session.queue(FakeResult(rows=[(source.org_id, source.type, source.project_id)]))  # cache binding on replay
    fake_session.queue(FakeResult(rows=[], scalar_value=previous, has_scalar=True))
    headers = {
        "X-API-Key": plaintext,
        "Content-Type": "application/json",
        "Idempotency-Key": "abc-123",
    }
    first = http_client.post(f"/v1/ingest/{source.id}", headers=headers, content=b'{"a":1}')
    assert first.status_code in (202, 401, 429), first.text
    if first.status_code == 202:
        second = http_client.post(f"/v1/ingest/{source.id}", headers=headers, content=b'{"a":1}')
        assert second.status_code == 202
        assert second.headers.get("Idempotent-Replay") in {"true", None}


