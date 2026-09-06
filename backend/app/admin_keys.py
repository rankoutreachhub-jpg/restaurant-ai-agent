"""
Key generation/verification for restaurant-scoped admin users (Stage 3
Step 3 — multi-tenant authorization).

Issued keys look like "{key_id}.{secret}", e.g. "ra_7f3c2a91.k3n9...".
key_id is a short, non-secret, indexed lookup handle stored in plaintext
on the AdminUser row; secret is never stored — only sha256(secret) is,
as key_hash. This means verifying a presented key is an indexed lookup
by key_id followed by exactly one constant-time hash comparison,
instead of scanning every AdminUser row and comparing against each one
(which would be both slower and a timing side-channel across rows).

sha256 (not a slow password-hashing KDF like bcrypt/scrypt) is
appropriate here because the input is a high-entropy random secret we
generate ourselves, not a low-entropy human-chosen password — there is
no offline-guessing risk a slow KDF would need to defend against, only
a need to avoid storing the plaintext.
"""

import hashlib
import secrets

KEY_PREFIX = "ra"  # short for "restaurant admin" — purely cosmetic, not secret


def _hash_secret(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def generate_key() -> tuple[str, str, str]:
    """
    Returns (plaintext_key, key_id, key_hash) for a freshly issued admin
    user. plaintext_key is what the caller is given (once); key_id and
    key_hash are what gets stored on the AdminUser row.
    """
    key_id = f"{KEY_PREFIX}_{secrets.token_hex(8)}"
    secret = secrets.token_urlsafe(32)
    plaintext_key = f"{key_id}.{secret}"
    return plaintext_key, key_id, _hash_secret(secret)


def split_key(api_key: str) -> tuple[str, str] | None:
    """Splits a presented key into (key_id, secret). Returns None if the
    key isn't in the expected "{key_id}.{secret}" shape."""
    if "." not in api_key:
        return None
    key_id, _, secret = api_key.partition(".")
    if not key_id or not secret:
        return None
    return key_id, secret


def verify_secret(secret: str, key_hash: str) -> bool:
    """Constant-time comparison of a presented secret against a stored hash."""
    return secrets.compare_digest(_hash_secret(secret), key_hash)
