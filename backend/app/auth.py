"""
Authentication for admin endpoints.

MVP approach: a shared secret (ADMIN_API_KEY, set in .env) that must be
sent in the "X-Admin-API-Key" header on every /admin/* request. This is
deliberately simple (no user accounts, sessions, or JWTs) but avoids
common pitfalls of ad-hoc auth:
  - each candidate key is compared with a constant-time comparison to
    avoid leaking information via response-timing side channels
  - it is only ever read from a header, never from a query string or
    body, so it can't end up in server access logs or browser history
  - a missing key and a wrong key both return 401 with no hint about
    which one it was, so the endpoint can't be used to test guesses
  - the key itself is never included in that 401 response or in any
    log line — only the fact that a request was rejected

Key rotation: an optional second secret, ADMIN_API_KEY_PREVIOUS, is
accepted alongside the current ADMIN_API_KEY. This lets you rotate the
key without a hard cutover: set ADMIN_API_KEY_PREVIOUS to the old value
and ADMIN_API_KEY to a freshly generated one, and both work until every
client has switched over — then remove ADMIN_API_KEY_PREVIOUS to fully
revoke the old key. See README.md for the full rotation procedure.
"""

import logging
import secrets

from fastapi import HTTPException, Request, Security, status
from fastapi.security import APIKeyHeader

from . import config

logger = logging.getLogger(__name__)

_api_key_header = APIKeyHeader(name="X-Admin-API-Key", auto_error=False)


def _matches_configured_key(api_key: str) -> bool:
    """
    True if api_key matches the current admin key or, if one is
    configured, the previous key still being honoured during rotation.
    Both comparisons are constant-time; which one matched (if either)
    is never surfaced anywhere.
    """
    if secrets.compare_digest(api_key, config.ADMIN_API_KEY):
        return True
    if config.ADMIN_API_KEY_PREVIOUS and secrets.compare_digest(
        api_key, config.ADMIN_API_KEY_PREVIOUS
    ):
        return True
    return False


def verify_admin_key(request: Request, api_key: str = Security(_api_key_header)) -> None:
    """FastAPI dependency: raises 401 unless a valid admin key was supplied."""
    if not api_key or not _matches_configured_key(api_key):
        # Log the fact of a rejected attempt (client + path) as a
        # security-relevant event — never the submitted key value itself.
        client = request.client.host if request.client else "unknown"
        logger.warning(
            "Admin authentication failed (client=%s path=%s)",
            client,
            request.url.path,
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid admin API key. Include a valid key in "
                   "the 'X-Admin-API-Key' header.",
            headers={"WWW-Authenticate": "X-Admin-API-Key"},
        )
