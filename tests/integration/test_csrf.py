"""CSRF double-submit enforcement (ADR-004 point 4, SEC-12).

Every unsafe method must reject a missing or mismatched CSRF token with 403.
A safe GET must never require one.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


def test_get_does_not_require_csrf(http_client, app_factory, owner_a) -> None:  # noqa: ANN001
    """Reads must stay simple: no CSRF header on a GET."""
    app_factory.login_as(owner_a)
    response = http_client.get("/v1/projects")
    assert response.status_code == 200, "GET with a valid session must succeed without CSRF."


def test_post_without_csrf_header_is_403(http_client, app_factory, owner_a) -> None:  # noqa: ANN001
    app_factory.login_as(owner_a)
    response = http_client.post(
        "/v1/projects",
        headers={"Content-Type": "application/json"},
        json={"name": "Tanpa CSRF"},
    )
    assert response.status_code == 403
    body = response.json()
    assert body["code"] == "forbidden"
    assert "csrf" in body["message"].lower() or "CSRF" in body["message"], (
        "Pesan harus menyebut CSRF agar bisa didiagnosis."
    )


def test_post_with_mismatched_csrf_cookie_and_header_is_403(http_client, app_factory, owner_a) -> None:  # noqa: ANN001
    """Double-submit fails when the header token is not the one bound to the session."""
    app_factory.login_as(owner_a)
    response = http_client.post(
        "/v1/projects",
        headers={
            "Content-Type": "application/json",
            "Cookie": f"dl_session={owner_a.session_token}; dl_csrf={owner_a.csrf_token}",
            "X-CSRF-Token": "attacker-supplied-token",
        },
        json={"name": "Palsu"},
    )
    assert response.status_code == 403


def test_post_with_valid_csrf_is_not_forbidden(http_client, app_factory, owner_a) -> None:  # noqa: ANN001
    """Positive control: with a proper token the request reaches the handler.

    We assert only that CSRF did not block it (403 is the failure we test for);
    the handler may still fail on the fake session, which is not this test's
    concern.
    """
    app_factory.login_as(owner_a)
    response = http_client.post(
        "/v1/projects",
        headers={
            "Content-Type": "application/json",
            "Cookie": f"dl_session={owner_a.session_token}; dl_csrf={owner_a.csrf_token}",
            "X-CSRF-Token": owner_a.csrf_token,
        },
        json={"name": "Proyek sah"},
    )
    assert response.status_code != 403, "Token CSRF yang benar tidak boleh ditolak."
