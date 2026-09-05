"""
Authentication and platform-level authorization for admin endpoints.

Two admin identities exist:

  1. Platform superadmin — a shared secret (ADMIN_API_KEY, set in .env,
     with an optional ADMIN_API_KEY_PREVIOUS during rotation) sent in
     the "X-Admin-API-Key" header. This is unchanged from Stage 1-2 and
     has implicit access to every restaurant — intended for platform
     operations (onboarding a restaurant, issuing its first scoped
     admin key), not routine day-to-day tenant admin work.

  2. Restaurant-scoped admin (Stage 3 Step 3) — a per-admin-user API key
     issued via the /admin/platform/admin-users endpoints (see
     app/routers/platform_admin.py and app/admin_keys.py), stored only
     as a salted hash, granting access to one or more specific
     restaurants via AdminRestaurantAccess rows (see app/models.py).
     Which restaurant_id(s) a given request may actually touch is then
     enforced by app/authz.require_restaurant_access — this module only
     answers "who is this", not "what may they touch".

Shared security properties, preserved from the original design:
  - every candidate key is compared with a constant-time comparison to
    avoid leaking information via response-timing side channels
  - keys are only ever read from a header, never a query string or
    body, so they can't end up in server access logs or browser history
  - a missing key, a wrong key, and a deactivated/unknown scoped key all
    return the same 401 with no hint about which one it was
  - no key (superadmin or scoped) is ever included in a response body
    or a log line — only the fact that a request was rejected or, for
    scoped keys, which key_id (a public, non-secret handle) was used
"""

import logging
import secrets
from dataclasses import dataclass
from typing import FrozenSet, Optional

from fastapi import Depends, HTTPException, Request, Security, status
from fastapi.security import APIKeyHeader
from sqlalchemy.orm import Session

from . import config, models
from .admin_keys import split_key, verify_secret
from .database import get_db

logger = logging.getLogger(__name__)

_api_key_header = APIKeyHeader(name="X-Admin-API-Key", auto_error=False)


def _matches_configured_key(api_key: str) -> bool:
    """
    True if api_key matches the current platform superadmin key or, if
    one is configured, the previous key still being honoured during
    rotation. Both comparisons are constant-time; which one matched (if
    either) is never surfaced anywhere.
    """
    if secrets.compare_digest(api_key, config.ADMIN_API_KEY):
        return True
    if config.ADMIN_API_KEY_PREVIOUS and secrets.compare_digest(
        api_key, config.ADMIN_API_KEY_PREVIOUS
    ):
        return True
    return False


@dataclass(frozen=True)
class AdminIdentity:
    """Who is making this admin request. `allowed_restaurant_ids` is
    None for a superadmin (meaning "every restaurant"), otherwise the
    concrete set of restaurant_ids this admin_user is scoped to."""

    is_superadmin: bool
    admin_user_id: Optional[int] = None
    label: Optional[str] = None
    allowed_restaurant_ids: Optional[FrozenSet[int]] = None

    def may_access(self, restaurant_id: int) -> bool:
        return self.is_superadmin or (
            self.allowed_restaurant_ids is not None
            and restaurant_id in self.allowed_restaurant_ids
        )


def _unauthorized() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Missing or invalid admin API key. Include a valid key in "
               "the 'X-Admin-API-Key' header.",
        headers={"WWW-Authenticate": "X-Admin-API-Key"},
    )


def _resolve_scoped_admin(db: Session, api_key: str) -> Optional[AdminIdentity]:
    """Looks up a restaurant-scoped admin key. Returns None (never
    raises) if the key doesn't match the scoped-key shape, doesn't
    exist, is inactive, or the secret is wrong — the caller treats all
    of those identically, as "not a valid key"."""
    split = split_key(api_key)
    if split is None:
        return None
    key_id, secret = split

    admin_user = (
        db.query(models.AdminUser)
        .filter(models.AdminUser.key_id == key_id)
        .first()
    )
    if not admin_user or not admin_user.is_active:
        return None
    if not verify_secret(secret, admin_user.key_hash):
        return None

    allowed = frozenset(
        access.restaurant_id for access in admin_user.restaurant_access
    )
    return AdminIdentity(
        is_superadmin=False,
        admin_user_id=admin_user.id,
        label=admin_user.label,
        allowed_restaurant_ids=allowed,
    )


def get_current_admin(
    request: Request,
    api_key: str = Security(_api_key_header),
    db: Session = Depends(get_db),
) -> AdminIdentity:
    """FastAPI dependency: resolves and returns the calling admin's
    identity, or raises 401 if no valid key (superadmin or scoped) was
    supplied. Use this in place of the old verify_admin_key wherever a
    handler needs to know *who* is calling, then pass the result to
    app.authz.require_restaurant_access to check *what* they may touch."""
    if api_key and _matches_configured_key(api_key):
        return AdminIdentity(is_superadmin=True)

    scoped_identity = _resolve_scoped_admin(db, api_key) if api_key else None
    if scoped_identity is not None:
        return scoped_identity

    # Log the fact of a rejected attempt (client + path) as a
    # security-relevant event — never the submitted key value itself.
    client = request.client.host if request.client else "unknown"
    logger.warning(
        "Admin authentication failed (client=%s path=%s)",
        client,
        request.url.path,
    )
    raise _unauthorized()


def verify_admin_key(
    current_admin: AdminIdentity = Depends(get_current_admin),
) -> None:
    """Back-compat shim for the pre-Stage-3-Step-3 dependency shape
    (auth only, no identity needed). Not used by any current router —
    kept only in case anything external still imports it."""
    return None


def require_superadmin(
    current_admin: AdminIdentity = Depends(get_current_admin),
) -> AdminIdentity:
    """FastAPI dependency: raises 403 unless the caller is the platform
    superadmin. Used to protect the platform-admin endpoints (creating
    restaurants and admin users) — a restaurant-scoped admin key, no
    matter how many restaurants it's scoped to, is never sufficient."""
    if not current_admin.is_superadmin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This operation requires the platform superadmin key.",
        )
    return current_admin
