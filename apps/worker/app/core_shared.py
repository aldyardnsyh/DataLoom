"""Shared helpers the worker needs from the API's encryption scheme.

Duplicated deliberately (not imported) because the worker is a separate
deployable; the derivation MUST match ``apps/api/app/services/source_secrets.py``.
If you change one, change both and bump ``v`` in the stored wrapper.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
from typing import Any

from cryptography.fernet import Fernet, InvalidToken


def _fernet() -> Fernet:
    # Worker uses the SAME SECRET_KEY as the API (passed through env).
    secret = os.environ.get("SECRET_KEY", "")
    if not secret:
        raise RuntimeError("SECRET_KEY wajib diset untuk membaca konfigurasi sumber.")
    digest = hashlib.sha256(f"dataloom-source-secret:{secret}".encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def decrypt_source_config(stored: dict[str, Any]) -> dict[str, Any]:
    token = (stored or {}).get("ciphertext")
    if not token:
        raise ValueError("Konfigurasi sumber tidak memiliki ciphertext.")
    try:
        plaintext = _fernet().decrypt(token.encode("ascii"))
    except InvalidToken as exc:
        raise ValueError("Konfigurasi sumber tidak dapat didekripsi.") from exc
    return json.loads(plaintext.decode("utf-8"))
