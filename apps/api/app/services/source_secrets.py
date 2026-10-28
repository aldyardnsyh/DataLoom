"""Encryption of source secrets at rest (FR-015, PRD section 9.8).

Fernet (AES-128-CBC + HMAC) keyed from ``SECRET_KEY`` via HKDF-like SHA-256
derivation. ``config_enc`` therefore contains ciphertext, never plaintext.
"""

from __future__ import annotations

import base64
import hashlib
import json
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from app.config import get_settings
from app.core.errors import ValidationProblem


def _fernet() -> Fernet:
    settings = get_settings()
    digest = hashlib.sha256(
        f"dataloom-source-secret:{settings.SECRET_KEY}".encode("utf-8")
    ).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_config(config: dict[str, Any]) -> dict[str, Any]:
    """Serialise + encrypt. Returns a JSONB-safe wrapper."""
    token = _fernet().encrypt(
        json.dumps(config, separators=(",", ":"), sort_keys=True).encode("utf-8")
    )
    return {"v": 1, "alg": "fernet", "ciphertext": token.decode("ascii")}


def decrypt_config(stored: dict[str, Any]) -> dict[str, Any]:
    """Decrypt a wrapper produced by :func:`encrypt_config`."""
    token = stored.get("ciphertext") if isinstance(stored, dict) else None
    if not token:
        raise ValidationProblem("Konfigurasi sumber rusak (format tidak dikenal).")
    try:
        plaintext = _fernet().decrypt(token.encode("ascii"))
    except InvalidToken as exc:
        raise ValidationProblem(
            "Konfigurasi sumber tidak dapat didekripsi. Kunci enkripsi mungkin berubah."
        ) from exc
    return json.loads(plaintext.decode("utf-8"))


#: Config keys that are secret and must never be returned to the client.
SECRET_CONFIG_KEYS = frozenset({"password", "dsn", "api_token", "headers"})


def redact_config(config: dict[str, Any]) -> dict[str, Any]:
    """Return a client-safe copy: secrets replaced by a boolean marker."""
    safe: dict[str, Any] = {}
    for key, value in config.items():
        if key in SECRET_CONFIG_KEYS:
            safe[key] = "***" if value else None
        elif key == "url" and isinstance(value, str):
            safe[key] = value  # non-secret, needed by the UI
        else:
            safe[key] = value
    return safe
