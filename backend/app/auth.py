"""
Authentication for admin endpoints.

MVP approach: a single shared secret (ADMIN_API_KEY, set in .env) that
must be sent in the "X-Admin-API-Key" header on every /admin/* request.
This is deliberately simple (no user accounts, sessions, or JWTs) but
avoids common pitfalls of ad-hoc auth:
  - the key is compared with a constant-time comparison to avoid
    leaking information via response-timing side channels
  - it is only ever read from a header, never from a query string or
    body, so it can't end up in server access logs or browser history
  - a missing key and a wrong key both return 401 with no hint about
    which one it was, so the endpoint can't be used to test guesses
"""

import secrets

from fastapi import HTTPException, Security, status
from fastapi.security import APIKeyHeader

from . import config

_api_key_header = APIKeyHeader(name="X-Admin-API-Key", auto_error=False)


def verify_admin_key(api_key: str = Security(_api_key_header)) -> None:
    """FastAPI dependency: raises 401 unless a valid admin key was supplied."""
    if not api_key or not secrets.compare_digest(api_key, config.ADMIN_API_KEY):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid admin API key. Include a valid key in "
                   "the 'X-Admin-API-Key' header.",
            headers={"WWW-Authenticate": "X-Admin-API-Key"},
        )
