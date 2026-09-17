"""
Paddle webhook signature verification and event processing (Jantar SaaS
Phase 4 — subscription provisioning; see app/routers/paddle.py for the
POST /webhooks/paddle endpoint that calls into this module, and
app/models.py:PaddleWebhookEvent/Subscription/SubscriptionEvent for the
underlying schema).

THE RESTAURANT-ASSOCIATION MODEL (read this before changing resolution
logic below):
This codebase has no customer-account model and no self-signup flow, so
a webhook notification has no INHERENTLY reliable way to name which
restaurant (if any) it belongs to — an email-address match would be a
GUESS (the checkout email need not match a restaurant's own contact
email), and this module deliberately never guesses. See
_resolve_restaurant_id below for the only two links it will ever trust:
  1. This Paddle subscription id is already linked to a restaurant's
     Subscription row (a later lifecycle event for a subscription this
     module itself linked before via link #2, at some earlier point).
  2. The notification's data.custom_data.checkout_token verifies (see
     app/paddle_checkout_tokens.py:verify_checkout_token) — a short-
     lived token this platform's OWN backend issued via
     POST /admin/restaurant/{id}/checkout-session, only after the
     requesting admin had already passed the existing admin
     authentication AND restaurant-scoping checks
     (get_current_admin + require_restaurant_access). Only once the
     signature and expiry check out is the restaurant_id EMBEDDED
     INSIDE the verified token trusted.
A bare, unsigned data.custom_data.restaurant_id is deliberately NEVER
trusted, on its own, under any circumstance — a Paddle client-side
token is public by design (see frontend/pricing.html), so nothing stops
anyone from calling Paddle's own Checkout SDK directly with our public
token and an arbitrary, attacker-chosen custom_data; trusting an
unsigned restaurant_id straight out of that would let anyone activate
or modify ANY restaurant's subscription by guessing its (small,
sequential) id. This was flagged and closed as part of the approved
authenticated-checkout-association plan — see
app/paddle_checkout_tokens.py's own docstring for the full reasoning.
Anything that resolves to neither link above is durably logged
(PaddleWebhookEvent, restaurant_id=NULL) and otherwise ignored: no
restaurant is guessed, no account or restaurant is auto-created. This
correctly covers a checkout started from the still-public, still-
unauthenticated frontend/pricing.html (see that page — it never sends
any custom_data at all), which is expected to remain unassociated and
reconciled manually, exactly as before this module gained link #2.

IDEMPOTENCY AND ORDERING:
Every notification Paddle delivers carries a unique event_id — Paddle
retries on anything but a 2xx response, so the SAME event_id can arrive
more than once. process_paddle_webhook_event checks
PaddleWebhookEvent.event_id first and no-ops (still returning success)
on a repeat. Paddle also does not guarantee delivery ORDER, so before
applying a state change this module compares the incoming notification's
occurred_at against the latest occurred_at of a previously APPLIED
notification for the same Paddle subscription id, and skips applying
(while still recording receipt) anything older — see
_is_stale_for_subscription.
"""

import hashlib
import hmac
import json
import logging
from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

from . import config, models
from .paddle_checkout_tokens import verify_checkout_token
from .plans import PlanCode

logger = logging.getLogger(__name__)

# The three real, live Paddle monthly price ids (see frontend/pricing.html
# and backend/tests/test_frontend_static_pages.py) mapped to this
# platform's own plan codes. Deliberately the only price ids this module
# will ever map — an id not in this dict is logged, never guessed at
# (see _map_price_id_to_plan_code).
PADDLE_PRICE_ID_TO_PLAN_CODE: dict[str, PlanCode] = {
    "pri_01m2gvtqa1h6ecyq45nt61wkk8": "starter",
    "pri_01m2gw4307t3zt670b86mfzs5d": "growth",
    "pri_01m2gw98h023yskcr1cfw3qhw6": "pro",
}

# Paddle's own subscription-status vocabulary (Paddle Billing API) mapped
# to this platform's existing status vocabulary (see
# app/subscriptions.py:SUBSCRIPTION_STATUSES). "paused" has no clean
# equivalent here (Paddle pauses a subscription while keeping it
# billable-later, unlike a hard cancellation) — treated as "past_due"
# (blocks new AI service, same as a real payment problem) rather than
# invented as a new status value this codebase doesn't otherwise know.
PADDLE_STATUS_TO_SUBSCRIPTION_STATUS: dict[str, str] = {
    "active": "active",
    "trialing": "trialing",
    "past_due": "past_due",
    "paused": "past_due",
    "canceled": "canceled",
}

# The event types this module knows how to apply a subscription state
# change for. Anything else Paddle might send (e.g. transaction.created,
# subscription.paused) is durably logged like any other event (see
# process_paddle_webhook_event) but never applied.
_SUBSCRIPTION_LIFECYCLE_EVENT_TYPES = frozenset({
    "subscription.created", "subscription.updated",
    "subscription.canceled", "subscription.past_due",
})
_HANDLED_EVENT_TYPES = _SUBSCRIPTION_LIFECYCLE_EVENT_TYPES | {"transaction.completed"}


def verify_paddle_signature(raw_body: bytes, signature_header: str) -> bool:
    """
    Constant-time verification of Paddle's "Paddle-Signature" header
    (format: "ts=<unix timestamp>;h1=<hex HMAC-SHA256>") against the RAW
    request body, exactly mirroring app/routers/whatsapp.py's existing
    X-Hub-Signature-256 check in spirit: fails closed (an unset
    PADDLE_WEBHOOK_SECRET means every POST is rejected, never accidentally
    accepted), computed over the raw bytes actually received (never a
    re-serialized JSON object), checked before anything else touches the
    payload.

    Per Paddle's documented scheme, the signed string is
    "{ts}:{raw_body}" (a literal colon between the timestamp and the raw
    request body), HMAC-SHA256'd with the webhook destination's signing
    secret.
    """
    if not config.PADDLE_WEBHOOK_SECRET:
        return False
    if not signature_header:
        return False

    parts: dict[str, str] = {}
    for item in signature_header.split(";"):
        if "=" not in item:
            continue
        key, _, value = item.partition("=")
        parts[key.strip()] = value.strip()

    ts = parts.get("ts")
    h1 = parts.get("h1")
    if not ts or not h1:
        return False

    signed_payload = ts.encode("utf-8") + b":" + raw_body
    expected = hmac.new(
        config.PADDLE_WEBHOOK_SECRET.encode("utf-8"), signed_payload, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, h1)


def _parse_paddle_timestamp(value: Optional[str]) -> Optional[datetime]:
    """Paddle timestamps are ISO 8601, e.g. "2026-09-17T12:00:00.000000Z".
    Returns None (never raises) on anything unparseable, so a malformed
    or missing timestamp degrades gracefully rather than rejecting an
    otherwise-valid webhook."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
    except (ValueError, TypeError):
        return None


def _extract_price_id(data: dict) -> Optional[str]:
    """The first line item's price id, from either a subscription object
    or a transaction object — both shapes nest it the same way
    (data.items[0].price.id). This platform only ever has one plan per
    subscription, so the first item is authoritative."""
    items = data.get("items") or []
    if not items:
        return None
    price = (items[0] or {}).get("price") or {}
    return price.get("id")


def _map_price_id_to_plan_code(price_id: Optional[str]) -> Optional[PlanCode]:
    if not price_id:
        return None
    return PADDLE_PRICE_ID_TO_PLAN_CODE.get(price_id)


def _extract_current_period_end(data: dict) -> Optional[datetime]:
    current_billing_period = data.get("current_billing_period") or {}
    return (
        _parse_paddle_timestamp(current_billing_period.get("ends_at"))
        or _parse_paddle_timestamp(data.get("next_billed_at"))
    )


def _resolve_restaurant_id(db: Session, data: dict, provider_subscription_id: Optional[str]) -> Optional[int]:
    """
    The ONLY two deterministic links this module ever trusts — see this
    module's docstring for why an email or name match is never
    attempted, and why a bare custom_data.restaurant_id is never one of
    them. Returns None (never guesses, never creates a restaurant) when
    neither applies.
    """
    if provider_subscription_id:
        existing = (
            db.query(models.Subscription)
            .filter(models.Subscription.provider_subscription_id == provider_subscription_id)
            .first()
        )
        if existing:
            return existing.restaurant_id

    custom_data = data.get("custom_data") or {}
    checkout_token = custom_data.get("checkout_token")
    if not checkout_token or not isinstance(checkout_token, str):
        return None

    restaurant_id = verify_checkout_token(checkout_token)
    if restaurant_id is None:
        return None

    restaurant = db.query(models.Restaurant).filter(models.Restaurant.id == restaurant_id).first()
    return restaurant.id if restaurant else None


def _is_stale_for_subscription(db: Session, provider_subscription_id: str, occurred_at: Optional[datetime]) -> bool:
    """True if a PREVIOUSLY APPLIED notification for this same Paddle
    subscription is already newer than this one — Paddle does not
    guarantee delivery order. A notification with no parseable
    occurred_at is never treated as stale (nothing to compare against),
    matching _parse_paddle_timestamp's graceful-degradation stance."""
    if occurred_at is None:
        return False
    latest_applied = (
        db.query(models.PaddleWebhookEvent.occurred_at)
        .filter(
            models.PaddleWebhookEvent.provider_subscription_id == provider_subscription_id,
            models.PaddleWebhookEvent.applied.is_(True),
            models.PaddleWebhookEvent.occurred_at.isnot(None),
        )
        .order_by(models.PaddleWebhookEvent.occurred_at.desc())
        .first()
    )
    return bool(latest_applied and latest_applied[0] > occurred_at)


def process_paddle_webhook_event(db: Session, payload: dict) -> dict:
    """
    Processes one already signature-verified Paddle webhook notification.
    Always returns a small result dict describing what happened (never
    raises for a business-logic outcome — an unknown price id, an
    unresolvable restaurant, and a stale/duplicate event are all
    NORMAL, expected outcomes that still get a 200 back to Paddle, so it
    never retries something retrying can't fix). The caller
    (app/routers/paddle.py) only needs to translate signature/JSON
    failures into an HTTP error status — everything past that point is
    "received", by design.

    result["outcome"] is one of:
      "duplicate"            — this event_id was already processed.
      "stale"                — a newer notification for this Paddle
                                subscription was already applied; this
                                one was recorded but not applied.
      "unassociated"         — recorded, but no restaurant could be
                                determined (see this module's docstring).
      "applied"              — the matched restaurant's Subscription row
                                (and a SubscriptionEvent) were updated.
      "ignored_event_type"   — a well-formed, signature-valid Paddle
                                event this module does not act on (only
                                the 5 required event types are handled;
                                anything else is logged and ignored).
    """
    event_id = payload.get("event_id")
    event_type = payload.get("event_type")
    occurred_at = _parse_paddle_timestamp(payload.get("occurred_at"))
    data = payload.get("data") or {}

    if not event_id or not event_type:
        logger.warning("Paddle webhook rejected: missing event_id or event_type")
        return {"outcome": "invalid", "event_type": event_type}

    if db.query(models.PaddleWebhookEvent).filter(models.PaddleWebhookEvent.event_id == event_id).first():
        logger.info("Paddle webhook duplicate ignored (event_id=%s event_type=%s)", event_id, event_type)
        return {"outcome": "duplicate", "event_type": event_type}

    if event_type not in _HANDLED_EVENT_TYPES:
        db.add(models.PaddleWebhookEvent(
            event_id=event_id, event_type=event_type, occurred_at=occurred_at,
            provider_subscription_id=data.get("id") or data.get("subscription_id"),
            restaurant_id=None, applied=False, payload=json.dumps(payload),
        ))
        db.commit()
        logger.info("Paddle webhook received for unhandled event_type=%s (event_id=%s)", event_type, event_id)
        return {"outcome": "ignored_event_type", "event_type": event_type}

    provider_subscription_id = (
        data.get("id") if event_type in _SUBSCRIPTION_LIFECYCLE_EVENT_TYPES else data.get("subscription_id")
    )

    if provider_subscription_id and _is_stale_for_subscription(db, provider_subscription_id, occurred_at):
        db.add(models.PaddleWebhookEvent(
            event_id=event_id, event_type=event_type, occurred_at=occurred_at,
            provider_subscription_id=provider_subscription_id,
            restaurant_id=None, applied=False, payload=json.dumps(payload),
        ))
        db.commit()
        logger.info(
            "Paddle webhook stale/out-of-order, not applied (event_id=%s event_type=%s subscription_id=%s)",
            event_id, event_type, provider_subscription_id,
        )
        return {"outcome": "stale", "event_type": event_type}

    restaurant_id = _resolve_restaurant_id(db, data, provider_subscription_id)

    if restaurant_id is None:
        db.add(models.PaddleWebhookEvent(
            event_id=event_id, event_type=event_type, occurred_at=occurred_at,
            provider_subscription_id=provider_subscription_id,
            restaurant_id=None, applied=False, payload=json.dumps(payload),
        ))
        db.commit()
        logger.warning(
            "Paddle webhook could not be associated with any restaurant "
            "(event_id=%s event_type=%s subscription_id=%s) -- see "
            "app/paddle_webhooks.py module docstring for the remaining "
            "association requirement.",
            event_id, event_type, provider_subscription_id,
        )
        return {"outcome": "unassociated", "event_type": event_type}

    subscription = (
        db.query(models.Subscription)
        .filter(models.Subscription.restaurant_id == restaurant_id)
        .first()
    )
    if subscription is None:
        # Should not happen (every restaurant has exactly one Subscription
        # row from creation — see app/subscriptions.py), but never invent
        # one here; that stays this module's one job it will not do
        # silently (see the module docstring's "no restaurant is
        # auto-created" stance, which extends to not auto-creating a
        # Subscription row via an unexpected path either).
        db.add(models.PaddleWebhookEvent(
            event_id=event_id, event_type=event_type, occurred_at=occurred_at,
            provider_subscription_id=provider_subscription_id,
            restaurant_id=restaurant_id, applied=False, payload=json.dumps(payload),
        ))
        db.commit()
        logger.error(
            "Paddle webhook resolved restaurant_id=%s but it has no Subscription row (event_id=%s)",
            restaurant_id, event_id,
        )
        return {"outcome": "unassociated", "event_type": event_type}

    old_plan_code = subscription.plan_code
    old_status = subscription.status

    price_id = _extract_price_id(data)
    plan_code = _map_price_id_to_plan_code(price_id)
    unknown_price_id = bool(price_id) and plan_code is None
    if plan_code is not None:
        subscription.plan_code = plan_code

    paddle_status = data.get("status")
    mapped_status = PADDLE_STATUS_TO_SUBSCRIPTION_STATUS.get(paddle_status) if paddle_status else None
    unknown_status = bool(paddle_status) and mapped_status is None
    if mapped_status is not None:
        subscription.status = mapped_status
    elif event_type == "transaction.completed" and subscription.status not in ("active", "trialing"):
        # A completed transaction for a subscription with no explicit
        # status field of its own (Paddle transactions don't carry a
        # subscription status) is itself evidence of successful payment.
        subscription.status = "active"

    subscription.billing_provider = "paddle"
    if data.get("customer_id"):
        subscription.provider_customer_id = data["customer_id"]
    if provider_subscription_id:
        subscription.provider_subscription_id = provider_subscription_id
    if price_id:
        subscription.provider_price_id = price_id
    period_end = _extract_current_period_end(data)
    if period_end is not None:
        subscription.current_period_end = period_end

    db.add(models.SubscriptionEvent(
        restaurant_id=restaurant_id,
        event_type=f"paddle_{event_type}",
        payload=json.dumps({
            "event_id": event_id,
            "old_plan_code": old_plan_code,
            "new_plan_code": subscription.plan_code,
            "old_status": old_status,
            "new_status": subscription.status,
            "provider_subscription_id": provider_subscription_id,
            "provider_price_id": price_id,
        }),
    ))

    if unknown_price_id:
        db.add(models.SubscriptionEvent(
            restaurant_id=restaurant_id,
            event_type="paddle_unknown_price_id",
            payload=json.dumps({"event_id": event_id, "price_id": price_id}),
        ))
        logger.warning("Paddle webhook has unmapped price_id=%s (event_id=%s) -- plan_code left unchanged", price_id, event_id)

    if unknown_status:
        db.add(models.SubscriptionEvent(
            restaurant_id=restaurant_id,
            event_type="paddle_unknown_status",
            payload=json.dumps({"event_id": event_id, "status": paddle_status}),
        ))
        logger.warning("Paddle webhook has unmapped status=%s (event_id=%s) -- status left unchanged", paddle_status, event_id)

    db.add(models.PaddleWebhookEvent(
        event_id=event_id, event_type=event_type, occurred_at=occurred_at,
        provider_subscription_id=provider_subscription_id,
        restaurant_id=restaurant_id, applied=True, payload=json.dumps(payload),
    ))
    db.commit()
    db.refresh(subscription)

    logger.info(
        "Paddle webhook applied (event_id=%s event_type=%s restaurant_id=%s plan_code=%s status=%s)",
        event_id, event_type, restaurant_id, subscription.plan_code, subscription.status,
    )
    return {"outcome": "applied", "event_type": event_type, "restaurant_id": restaurant_id}
