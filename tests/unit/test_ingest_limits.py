"""FR-010 / FR-011 / FR-012: ingest payload limits and body-size enforcement.

PRD §5.2:
- FR-012 "Batas ukuran request (default 256 KB, 1.000 baris) - lebih dari batas
  -> 413"
- FR-010 "menerima JSON object/array"

The functions under test (``app.services.ingest_service``) are pure: they take
bytes in, raise ``AppError`` subclasses out. Asserting the *exception class*
(not just a status number) keeps the test honest: the HTTP layer maps these
classes to status codes in exactly one place (``core/errors.py``).
"""

from __future__ import annotations

import json

import pytest


def _service():
    from api_app.services import ingest_service

    return ingest_service


def _errors():
    from api_app.core import errors

    return errors


# ---------------------------------------------------------------------------
# Body size (FR-012 first half: 256 KB)
# ---------------------------------------------------------------------------
def test_enforce_body_size_accepts_a_body_at_the_limit() -> None:
    service = _service()
    from api_app.config import get_settings

    limit = get_settings().INGEST_MAX_BODY_BYTES
    # Exactly at the limit must pass: the documented rule is "> limit -> 413".
    service.enforce_body_size(limit)
    service.enforce_body_size(0)


def test_enforce_body_size_rejects_over_the_limit_with_payload_too_large() -> None:
    service = _service()
    errors = _errors()
    from api_app.config import get_settings

    limit = get_settings().INGEST_MAX_BODY_BYTES
    with pytest.raises(errors.PayloadTooLarge) as excinfo:
        service.enforce_body_size(limit + 1)

    assert excinfo.value.status_code == 413, (
        f"Batas body harus 413 (FR-012), bukan {excinfo.value.status_code}."
    )
    assert str(limit) in excinfo.value.message, (
        "Pesan 413 tidak menyebut batas byte; klien tidak tahu angka yang dilanggar."
    )


def test_enforce_body_size_rejects_negative_content_length() -> None:
    """SEC-15 regression guard: a negative header is malformed, never 'small'."""
    service = _service()
    errors = _errors()

    with pytest.raises(errors.PayloadTooLarge):
        service.enforce_body_size(-1)


def test_enforce_body_size_treats_missing_content_length_as_unknown() -> None:
    """``None`` (chunked upload) cannot be judged here: must NOT raise (rule: middleware)."""
    service = _service()
    # Passing must be silent; the raw-body check in ``handle_ingest`` is the net.
    assert service.enforce_body_size(None) is None


# ---------------------------------------------------------------------------
# Row count (FR-012 second half: 1.000 rows)
# ---------------------------------------------------------------------------
def test_parse_payload_rejects_more_than_max_rows_with_413() -> None:
    service = _service()
    errors = _errors()
    from api_app.config import get_settings

    max_rows = get_settings().INGEST_MAX_ROWS
    body = json.dumps([{"i": index} for index in range(max_rows + 1)]).encode()

    with pytest.raises(errors.PayloadTooLarge) as excinfo:
        service.parse_payload(body)

    assert excinfo.value.status_code == 413, (
        f"Terlalu banyak baris harus 413 (FR-012), bukan {excinfo.value.status_code}."
    )
    assert str(max_rows) in excinfo.value.message, (
        "Pesan tidak menyebut jumlah baris maksimum."
    )


def test_parse_payload_accepts_exactly_max_rows() -> None:
    service = _service()
    from api_app.config import get_settings

    max_rows = get_settings().INGEST_MAX_ROWS
    rows = service.parse_payload(json.dumps([{"i": index} for index in range(max_rows)]).encode())

    assert len(rows) == max_rows, (
        f"Batas baris off-by-one: {len(rows)} diterima, seharusnya {max_rows}."
    )


# ---------------------------------------------------------------------------
# Shape (FR-010: object or array of objects)
# ---------------------------------------------------------------------------
def test_parse_payload_accepts_a_single_object_as_one_row() -> None:
    service = _service()

    rows = service.parse_payload(b'{"a": 1, "b": "dua"}')
    assert rows == [{"a": 1, "b": "dua"}], (
        "Satu objek JSON harus menjadi satu baris (FR-010)."
    )


def test_parse_payload_accepts_an_array_of_objects() -> None:
    service = _service()

    rows = service.parse_payload(b'[{"a": 1}, {"a": 2}]')
    assert rows == [{"a": 1}, {"a": 2}]


@pytest.mark.parametrize(
    "body",
    [
        b'"sebuah string"',
        b"42",
        b"true",
        b"null",
        b"[1, 2, 3]",  # array of scalars
        b'[{"ok": 1}, "bukan objek"]',  # mixed array
        b"[[1, 2]]",  # array of arrays
    ],
)
def test_parse_payload_rejects_non_object_rows_with_422(body: bytes) -> None:
    service = _service()
    errors = _errors()

    with pytest.raises(errors.ValidationProblem) as excinfo:
        service.parse_payload(body)

    assert excinfo.value.status_code == 422, (
        f"Bentuk payload salah harus 422, bukan {excinfo.value.status_code}."
    )


@pytest.mark.parametrize(
    "body",
    [
        b"{bukan json}",
        b'{"a": 1',  # truncated
        b"",
        b"\xff\xfe\x00binary",  # invalid UTF-8 / binary
    ],
)
def test_parse_payload_rejects_invalid_json_with_422(body: bytes) -> None:
    service = _service()
    errors = _errors()

    with pytest.raises(errors.ValidationProblem) as excinfo:
        service.parse_payload(body)

    assert excinfo.value.status_code == 422
    assert "JSON" in excinfo.value.message or "json" in excinfo.value.message, (
        "Pesan tidak menjelaskan bahwa JSON tidak valid."
    )


# ---------------------------------------------------------------------------
# Ordering invariant: row-count check happens BEFORE per-row type check
# ---------------------------------------------------------------------------
def test_row_count_limit_is_checked_before_element_types() -> None:
    """A huge array of scalars must be a 413 (cheap) not a 422 (per-row scan)."""
    service = _service()
    errors = _errors()
    from api_app.config import get_settings

    max_rows = get_settings().INGEST_MAX_ROWS
    body = json.dumps([None] * (max_rows + 1)).encode()

    with pytest.raises(errors.PayloadTooLarge):
        service.parse_payload(body)


# ---------------------------------------------------------------------------
# Stream naming (FR-010: one stream per org+source; isolation at the key level)
# ---------------------------------------------------------------------------
def test_stream_key_is_namespaced_by_org_and_source() -> None:
    from api_app.core.ratelimit import stream_key

    key = stream_key("org-1", "src-9")
    assert key == "ingest:org-1:src-9", (
        f"Nama stream {key!r} tidak sesuai PRD §9.4 ingest:{{org_id}}:{{source_id}}."
    )
    assert stream_key("org-2", "src-9") != key, (
        "Dua organisasi berbagi stream yang sama -> data lintas tenant tercampur."
    )
