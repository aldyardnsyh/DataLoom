"""FR-001 (Argon2id, session tokens) and FR-004 (API key format) - unit level.

Every assertion here can FAIL. If ``hash_password`` stopped returning Argon2id,
or ``parse_api_key`` accepted ``dl_a_b``, this file goes red. That is the point.

No database, no network: ``app.core.security`` is pure Python + argon2-cffi.
"""

from __future__ import annotations

import re

import pytest

# Markers are registered in tests/pytest.ini (--strict-markers is on).


# ---------------------------------------------------------------------------
# Argon2id password hashing (FR-001: "Password di-hash Argon2id")
# ---------------------------------------------------------------------------
def test_hash_password_uses_argon2id_and_is_not_the_plaintext() -> None:
    from api_app.core.security import hash_password

    digest = hash_password("kata-sandi-rahasia-123")

    assert digest.startswith("$argon2id$"), (
        f"FR-001 dilanggar: hash bukan Argon2id melainkan {digest[:20]!r}."
    )
    assert "kata-sandi-rahasia-123" not in digest, (
        "Kata sandi plaintext muncul di dalam hash; hashing tidak berjalan."
    )
    # Sanity: an Argon2 encoded hash has 6 '$'-separated fields.
    assert digest.count("$") == 5, f"Format hash Argon2 tidak lengkap: {digest!r}"


def test_verify_password_accepts_correct_and_rejects_wrong() -> None:
    from api_app.core.security import hash_password, verify_password

    digest = hash_password("benar-sekali-9999")

    assert verify_password("benar-sekali-9999", digest) is True, (
        "Kata sandi yang benar ditolak - autentikasi tidak akan pernah berhasil."
    )
    assert verify_password("salah-sekali-9999", digest) is False, (
        "Kata sandi salah diterima - kontrol autentikasi bocor (FR-001)."
    )


def test_verify_password_returns_false_instead_of_raising_on_garbage() -> None:
    from api_app.core.security import verify_password

    # A corrupted DB row must not produce a 500: it must be a failed login.
    for garbage in ("", "not-a-hash", "$argon2id$rusak", "$2b$12$abcdef"):
        assert verify_password("apa-saja", garbage) is False, (
            f"Hash rusak {garbage!r} tidak menghasilkan False; ini akan jadi 500."
        )


def test_two_hashes_of_the_same_password_differ_because_of_salt() -> None:
    from api_app.core.security import hash_password, verify_password

    first = hash_password("sandi-yang-sama-000")
    second = hash_password("sandi-yang-sama-000")

    assert first != second, "Dua hash identik => salt tidak acak (Argon2 salah konfigurasi)."
    assert verify_password("sandi-yang-sama-000", first)
    assert verify_password("sandi-yang-sama-000", second)


def test_needs_rehash_flags_legacy_and_invalid_hashes() -> None:
    from api_app.core.security import hash_password, needs_rehash

    strong = hash_password("sandi-kuat-12345")
    assert needs_rehash(strong) is False, (
        "Hash yang baru dibuat minta rehash: parameter Argon2 tidak konsisten."
    )
    # A bcrypt/legacy/invalid hash MUST be rehashed on next login, not trusted.
    assert needs_rehash("$2b$12$abcdefghijklmnopqrstuv") is True, (
        "Hash non-Argon2 tidak ditandai perlu rehash."
    )
    assert needs_rehash("bukan-hash-sama-sekali") is True, (
        "String acak tidak ditandai perlu rehash."
    )


# ---------------------------------------------------------------------------
# Session / CSRF tokens
# ---------------------------------------------------------------------------
def test_session_and_csrf_tokens_are_long_and_unique() -> None:
    from api_app.core.security import new_csrf_token, new_session_token

    sessions = {new_session_token() for _ in range(50)}
    csrf = {new_csrf_token() for _ in range(50)}

    assert len(sessions) == 50, "Token sesi berulang -> CSPRNG tidak dipakai."
    assert len(csrf) == 50, "Token CSRF berulang -> CSPRNG tidak dipakai."
    # 32 random bytes -> at least 40 url-safe chars (base64 of 32 bytes = 43).
    assert all(len(token) >= 40 for token in sessions), (
        "Token sesi lebih pendek dari 32 byte acak (entropi kurang)."
    )


# ---------------------------------------------------------------------------
# sha256_hex stability (the only stored form of sessions and API keys)
# ---------------------------------------------------------------------------
def test_sha256_hex_is_stable_and_matches_the_known_digest() -> None:
    from api_app.core.security import sha256_hex

    # Known-answer test: this value is part of the DB contract (String(64)).
    assert sha256_hex("abc") == (
        "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    ), "sha256_hex('abc') salah: SEMUA sesi & API key yang tersimpan jadi tidak cocok."
    assert sha256_hex("abc") == sha256_hex("abc"), "sha256_hex tidak deterministik."
    assert len(sha256_hex("apa pun")) == 64, "Panjang hash bukan 64 hex (kolom String(64))."
    assert sha256_hex("abc") != sha256_hex("abd"), "sha256_hex tidak sensitif input."


def test_sha256_hex_handles_non_ascii_without_raising() -> None:
    from api_app.core.security import sha256_hex

    digest = sha256_hex("kata sandi dengan émoji 🔐 dan aksen")
    assert re.fullmatch(r"[0-9a-f]{64}", digest), (
        f"Hash non-ASCII bukan hex 64 karakter: {digest!r}"
    )


# ---------------------------------------------------------------------------
# CSRF comparison (constant-time double-submit)
# ---------------------------------------------------------------------------
def test_constant_time_eq_matches_hmac_compare_digest_semantics() -> None:
    from api_app.core.security import constant_time_eq

    assert constant_time_eq("token-yang-sama", "token-yang-sama") is True
    assert constant_time_eq("token-yang-sama", "token-yang-samA") is False, (
        "Perbandingan CSRF menganggap token berbeda sebagai sama."
    )
    assert constant_time_eq("", "") is True
    assert constant_time_eq("", "x") is False
    assert constant_time_eq("é", "e") is False, "Perbandingan Unicode salah."


def test_constant_time_eq_does_not_raise_on_length_mismatch() -> None:
    from api_app.core.security import constant_time_eq

    # hmac.compare_digest requires both operands to support the buffer protocol
    # in the SAME way; a str/bytes mixture raises TypeError. Both operands here
    # are str, so it must return False, never raise.
    assert constant_time_eq("pendek", "jauh-lebih-panjang-dari-yang-lain") is False


# ---------------------------------------------------------------------------
# API key format: ``dl_<prefix>_<secret>`` (FR-004)
# ---------------------------------------------------------------------------
def test_generate_api_key_shape_matches_documented_format() -> None:
    from api_app.core.security import API_KEY_PREFIX_LEN, generate_api_key, sha256_hex

    plaintext, prefix, key_hash = generate_api_key()

    assert plaintext.startswith("dl_"), f"Key tidak berawalan 'dl_': {plaintext[:6]!r}"
    assert plaintext.count("_") == 2, (
        "Key harus punya tepat dua '_' (dl_<prefix>_<secret>); lebih dari itu ambigu."
    )
    _, parsed_prefix, secret = plaintext.split("_")
    assert parsed_prefix == prefix, "Prefix yang dikembalikan bukan bagian dari key."
    assert len(prefix) == API_KEY_PREFIX_LEN, (
        f"Panjang prefix {len(prefix)} != {API_KEY_PREFIX_LEN}."
    )
    assert key_hash == sha256_hex(secret), (
        "key_hash bukan sha256(secret): verifikasi key di ingest akan selalu gagal."
    )
    assert secret not in key_hash


@pytest.mark.parametrize(
    "raw",
    [
        "",  # empty
        "dl_",  # scheme only
        "dl_abcd",  # no secret
        "dl_abcd_",  # empty secret
        "_abcd_secretsecretsecret",  # no scheme
        "xx_abcd_secretsecretsecret",  # wrong scheme
        "dl_abc_secretsecretsecret",  # prefix too short (< 4)
        "dl_abcd_short",  # secret too short (< 16)
        "abcd_secretsecretsecret",  # no scheme prefix at all
        "dl_abcd_secret_secret",  # three underscores -> ambiguous
    ],
)
def test_parse_api_key_rejects_malformed_keys(raw: str) -> None:
    from api_app.core.security import parse_api_key

    assert parse_api_key(raw) is None, (
        f"FR-004 dilanggar: key rusak {raw!r} DITERIMA oleh parse_api_key. "
        "Bentuk ambigu bisa membuat secret dihitung dari potongan yang salah."
    )


def test_parse_api_key_accepts_a_well_formed_key() -> None:
    from api_app.core.security import generate_api_key, parse_api_key

    plaintext, prefix, _hash = generate_api_key()
    parsed = parse_api_key(plaintext)

    assert parsed is not None, "Key hasil generate_api_key() ditolak oleh parse_api_key()."
    assert parsed[0] == prefix, "Prefix hasil parse berbeda dari yang dikembalikan."
    assert plaintext == f"dl_{parsed[0]}_{parsed[1]}", "Rekonstruksi key tidak cocok."


def test_parse_api_key_is_strict_about_length_boundaries() -> None:
    from api_app.core.security import parse_api_key

    # 4-char prefix and 16-char secret are the documented MINIMUM.
    assert parse_api_key("dl_abcd_0123456789abcdef") == ("abcd", "0123456789abcdef")
    # One character below either minimum must be rejected.
    assert parse_api_key("dl_abc_0123456789abcdef") is None
    assert parse_api_key("dl_abcd_0123456789abcde") is None
