"""
Short-lived, signed checkout-authorization tokens (Jantar SaaS Phase 4.1
— authenticated Paddle checkout association; see the approved read-only
plan this implements).

WHY THIS EXISTS:
app/paddle_webhooks.py previously trusted a bare, unsigned
data.custom_data.restaurant_id straight out of a Paddle webhook payload.
That field is fully attacker-controlled: Paddle client-side tokens are
deliberately public (see frontend/pricing.html), so anyone can call
Paddle's own Checkout SDK directly with our public token and an
arbitrary custom_data — there is nothing Paddle-side stopping a forged
restaurant_id from reaching our webhook. This module closes that gap:
POST /admin/restaurant/{id}/checkout-session (app/routers/admin.py)
issues a token here ONLY after the caller has already passed this
platform's existing admin authentication AND restaurant-scoping checks
(get_current_admin + require_restaurant_access); the frontend then hands
that OPAQUE token to Paddle as custom_data.checkout_token instead of a
raw restaurant_id. app/paddle_webhooks.py verifies it here before
trusting the restaurant_id embedded inside it. A browser can never
forge a valid token for a restaurant it wasn't authorized for, because
it never has PADDLE_CHECKOUT_TOKEN_SECRET.

Deliberately a SEPARATE secret from PADDLE_WEBHOOK_SECRET (see
app/config.py's docstring for that variable for the full reasoning) —
this module never imports or touches app/paddle_webhooks.py's secret,
and that module never touches this one's.

TOKEN FORMAT (compact, URL-safe, stdlib only — no JWT library, no new
dependency):
    "<base64url(json({restaurant_id, exp, nonce}))>.<hex HMAC-SHA256>"
The HMAC is computed over the base64url payload segment itself (the
exact bytes sent to Paddle and echoed back), not over the decoded JSON —
so verification never needs to re-serialize anything, avoiding any
"same data, different bytes" mismatch class of bug.

Deliberately pure: no database session, no FastAPI/HTTP objects, no
network call — issue_checkout_token/verify_checkout_token are plain
functions over strings/ints, independently unit-testable exactly like
app/paddle_webhooks.py:verify_paddle_signature already is.
"""

import base64
import binascii
import hashlib
import hmac
import json
import secrets
import time
from typing import Optional

from . import config

# 15 minutes — long enough to complete a Paddle Checkout overlay,
# short enough to bound how long a captured-but-unused token stays
# valid. A reused (not just captured) token can only ever reapply the
# SAME restaurant_id it was signed for (the signature binds the two
# together), so there is no cross-tenant benefit to replay within this
# window — see the approved plan's analysis of why no additional
# single-use/nonce-consumption table is required.
TOKEN_LIFETIME_SECONDS = 15 * 60


def _b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _b64decode(data: str) -> bytes:
    # urlsafe_b64decode requires correct padding; token segments have
    # theirs stripped by _b64encode above, so it's restored here.
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)


def issue_checkout_token(restaurant_id: int) -> str:
    """
    Issues a signed token binding restaurant_id to a short expiry and a
    random nonce (entropy/uniqueness for logging and debugging, not a
    consumption-tracked value — see this module's docstring). Callers
    (app/routers/admin.py) are responsible for having already verified
    the caller may act on this restaurant_id — this function has no way
    to check that itself and does not try to.
    """
    payload = {
        "restaurant_id": restaurant_id,
        "exp": int(time.time()) + TOKEN_LIFETIME_SECONDS,
        "nonce": secrets.token_hex(16),
    }
    payload_b64 = _b64encode(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    signature = hmac.new(
        config.PADDLE_CHECKOUT_TOKEN_SECRET.encode("utf-8"), payload_b64.encode("ascii"), hashlib.sha256
    ).hexdigest()
    return f"{payload_b64}.{signature}"


def verify_checkout_token(token: str) -> Optional[int]:
    """
    Returns the verified restaurant_id, or None for ANY failure mode
    (missing/malformed token, wrong or unconfigured secret, tampered
    payload, tampered signature, expired token) — never raises, so a
    caller (app/paddle_webhooks.py) can treat "no trusted restaurant_id"
    uniformly regardless of exactly how a token failed to check out,
    exactly mirroring verify_paddle_signature's fail-closed style.
    """
    if not config.PADDLE_CHECKOUT_TOKEN_SECRET:
        return None
    if not token or "." not in token:
        return None

    payload_b64, _, signature = token.partition(".")
    if not payload_b64 or not signature:
        return None

    expected_signature = hmac.new(
        config.PADDLE_CHECKOUT_TOKEN_SECRET.encode("utf-8"), payload_b64.encode("ascii"), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(expected_signature, signature):
        return None

    try:
        payload = json.loads(_b64decode(payload_b64))
    except (ValueError, TypeError, binascii.Error):
        return None

    if not isinstance(payload, dict):
        return None

    exp = payload.get("exp")
    if not isinstance(exp, int) or exp < int(time.time()):
        return None

    restaurant_id = payload.get("restaurant_id")
    if not isinstance(restaurant_id, int) or isinstance(restaurant_id, bool):
        return None

    return restaurant_id
