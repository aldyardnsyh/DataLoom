"""Password hashing, session tokens, API keys, CSRF.

Threat notes (see docs/THREAT_MODEL.md):
- Passwords: Argon2id (FR-001). Parameters come from argon2-cffi defaults.
- Session tokens: 32 random bytes, stored only as SHA-256 so a DB dump does not
  yield usable sessions. Revocation = set ``revoked_at``.
- API keys: format ``dl_<prefix>_<secret>``; we store ``prefix`` (lookup) and
  ``sha256(secret)`` (verification). Plaintext is returned exactly once.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

_ph = PasswordHasher()  # Argon2id by default in argon2-cffi >= 21

API_KEY_PREFIX_LEN = 8
API_KEY_SECRET_LEN = 32
API_KEY_SCHEME = "dl"


# ---------------------------------------------------------------------------
# Passwords
# ---------------------------------------------------------------------------
def hash_password(password: str) -> str:
    """Argon2id hash. Encoded string starts with ``$argon2id$``."""
    return _ph.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """Constant-time-ish verification. Any argon2 failure means "wrong"."""
    try:
        return _ph.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    try:
        return _ph.check_needs_rehash(password_hash)
    except InvalidHashError:
        return True


# ---------------------------------------------------------------------------
# Tokens
# ---------------------------------------------------------------------------
def new_session_token() -> str:
    """Opaque token handed to the browser in an HttpOnly cookie."""
    return secrets.token_urlsafe(32)


def new_csrf_token() -> str:
    """CSRF token, readable by JS (double-submit pattern)."""
    return secrets.token_urlsafe(24)


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def constant_time_eq(a: str, b: str) -> bool:
    # hmac.compare_digest raises on non-ASCII str operands. Compare UTF-8 bytes
    # instead, which keeps the comparison constant-time and locale-independent.
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


# ---------------------------------------------------------------------------
# API keys
# ---------------------------------------------------------------------------
def generate_api_key() -> tuple[str, str, str]:
    """Create a new API key.

    Returns ``(plaintext, prefix, key_hash)`` where::

        plaintext = "dl_" + prefix + "_" + secret
        prefix    = 8 url-safe chars, unique, indexed for lookup
        key_hash  = sha256(secret)  # the only server-side copy of the secret
    """
    # Hex keeps the key alphabet free of ``_`` and ``-``. A urlsafe prefix or
    # secret can contain ``_``, which makes the documented three-part format
    # ambiguous and causes parse_api_key() to reject keys it just generated.
    prefix = secrets.token_hex(API_KEY_PREFIX_LEN // 2)
    secret = secrets.token_hex(API_KEY_SECRET_LEN)
    plaintext = f"{API_KEY_SCHEME}_{prefix}_{secret}"
    return plaintext, prefix, sha256_hex(secret)


def parse_api_key(raw: str) -> tuple[str, str] | None:
    """Split a raw key into ``(prefix, secret)``; ``None`` when malformed.

    Parsed strictly: no ``split`` on arbitrary ``_`` counts that would let a
    crafted key change how the secret is computed.
    """
    if not raw or not raw.startswith(API_KEY_SCHEME + "_"):
        return None
    parts = raw.split("_")
    if len(parts) != 3:
        return None
    _, prefix, secret = parts
    if len(prefix) < 4 or len(secret) < 16:
        return None
    return prefix, secret
