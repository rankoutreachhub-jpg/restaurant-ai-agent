"""
Subscription/plan enforcement (Jantar SaaS Phase 3 — see app/plans.py for
the plan catalog, app/subscriptions.py for the Phase 2 visibility helpers
this module builds on, and app/models.py:Subscription/SubscriptionEvent
for the underlying schema, both in place since Phase 1).

This is the one place that decides "may this restaurant's plan/status do
X right now" — mirroring app/authz.py's existing role as the one place
that decides "may this admin touch this restaurant". Every check function
below is self-contained (db + restaurant_id in, nothing but a return or an
HTTPException out) so each is independently unit-testable without needing
to construct a live request.

Every check function that BLOCKS an action raises fastapi.HTTPException
directly, with a `detail` shaped as {"error_code": ..., "message": ...}
rather than the plain string every other HTTPException in this codebase
uses — a deliberate, additive exception to that convention, so a caller
(the widget frontend, in particular) can render a specific "you've hit
your plan's limit" UI instead of parsing free-text. `error_code` values
are a small fixed vocabulary (see each function below), not meant to
grow arbitrarily.

Callers fall into two shapes:
  - A normal request handler (routers/chat.py, routers/widget.py,
    routers/platform_admin.py) lets the HTTPException propagate — FastAPI
    turns it into the response.
  - app/whatsapp_processing.py runs inside a BackgroundTasks callback with
    no request to respond to; it already catches HTTPException from
    whatsapp_rate_limiter.check() and logs-then-returns instead of
    raising further (see that module) — the same pattern applies here
    unchanged, so these functions need no special "background" mode.

SubscriptionEvent writes (Product Decision #6, this phase's audit): only
four triggers write a row — a PATCH-driven plan/status change
(record_subscription_change_event, called from
routers/platform_admin.py:update_restaurant_subscription), a conversation
quota block, an admin-user quota block, and a WhatsApp plan-gating block.
A subscription-status block (past_due/canceled) deliberately does NOT
write an event — that was a specific, considered omission in the approved
product decisions, not an oversight.
"""

import json
from typing import Tuple

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from . import models
from .plans import PLAN_LIMITS, PlanDefinition
from .subscriptions import current_period_conversation_count

# Statuses that may receive NEW AI-generated customer service (chat,
# widget chat, WhatsApp replies). Per Product Decision #3: active and
# trialing serve normally; past_due and canceled block new customer
# service but do NOT lock the restaurant admin dashboard or
# booking-management endpoints — this module is deliberately never
# wired into routers/admin.py for that reason.
ACTIVE_SERVICE_STATUSES = frozenset({"active", "trialing"})


def get_active_plan(db: Session, restaurant_id: int) -> Tuple[models.Subscription, PlanDefinition]:
    """
    Loads this restaurant's Subscription row and its plan definition
    together — every check below needs both. Raises 404 if somehow no
    Subscription row exists (not expected since Phase 2 — see
    app/subscriptions.py:create_subscription_for_restaurant — but this
    module never assumes it without checking, exactly like the existing
    _get_subscription_or_404 helpers in routers/admin.py and
    routers/platform_admin.py, which this deliberately mirrors rather
    than replaces, to avoid touching those unrelated endpoints).
    """
    subscription = (
        db.query(models.Subscription)
        .filter(models.Subscription.restaurant_id == restaurant_id)
        .first()
    )
    if not subscription:
        raise HTTPException(status_code=404, detail="No subscription exists for this restaurant yet")
    return subscription, PLAN_LIMITS[subscription.plan_code]


def check_subscription_status_allows_service(db: Session, restaurant_id: int) -> None:
    """
    Blocks NEW customer-facing AI service (not admin/booking access —
    see module docstring) when the subscription's status is not in
    ACTIVE_SERVICE_STATUSES. The message is deliberately generic and
    never repeats the internal status value back to an unauthenticated
    caller (a widget visitor has no business learning billing details).
    """
    subscription, _ = get_active_plan(db, restaurant_id)
    if subscription.status in ACTIVE_SERVICE_STATUSES:
        return
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail={
            "error_code": "subscription_inactive",
            "message": "This restaurant's AI chat service is currently unavailable. "
                       "Please contact the restaurant directly.",
        },
    )


def check_conversation_quota(db: Session, restaurant_id: int) -> None:
    """
    Blocks starting a NEW conversation once this restaurant has reached
    its plan's max_conversations_per_month for the current UTC calendar
    month (Product Decision #1) — an already-resumed conversation is
    never affected.

    Callers MUST invoke this BEFORE calling
    get_or_create_conversation/get_or_create_whatsapp_conversation for a
    request that turns out to be starting a new conversation — decide
    that first using the read-only, side-effect-free
    app/conversations.py:conversation_exists_for_token/
    whatsapp_conversation_exists. Calling this AFTER that flush would
    double-count the just-flushed row itself, since a flushed-but-
    uncommitted row is already visible to a COUNT(*) query within the
    same transaction — checking first avoids that entirely, and means
    this function never has anything of the caller's to roll back.

    Known, accepted tradeoff (Product Decision #7): this is a plain
    COUNT(*) read-then-act check with no row locking or atomic counter.
    Two concurrent requests can both read a count just under the limit
    and both proceed, allowing a small overshoot under real concurrent
    load. No Redis or new usage-counter table is introduced to close
    this gap — an exact hard cap is not worth the added infrastructure
    for this application's traffic volume.
    """
    subscription, plan = get_active_plan(db, restaurant_id)
    limit = plan["max_conversations_per_month"]
    usage = current_period_conversation_count(db, restaurant_id)
    if usage < limit:
        return

    db.add(models.SubscriptionEvent(
        restaurant_id=restaurant_id,
        event_type="conversation_quota_exceeded",
        payload=json.dumps({"plan_code": subscription.plan_code, "limit": limit, "usage": usage}),
    ))
    db.commit()
    raise HTTPException(
        status_code=status.HTTP_402_PAYMENT_REQUIRED,
        detail={
            "error_code": "conversation_quota_exceeded",
            "message": f"This restaurant has reached its monthly AI conversation limit "
                       f"({limit}) for the {plan['display_name']} plan.",
        },
    )


def check_admin_user_quota(db: Session, restaurant_id: int) -> None:
    """
    Blocks granting this restaurant ANOTHER admin-user access row once
    it already has max_admin_users (Product Decision #4). Only NEW
    grants are ever blocked — an existing grant is never touched or
    counted differently, so a restaurant that is already over its (newly
    lowered) limit after a downgrade keeps every existing admin; it just
    can't add more until it's back under the cap. Callers
    (routers/platform_admin.py:create_admin_user/grant_restaurant_access)
    are responsible for only calling this before a genuinely NEW grant,
    not for an idempotent re-grant of an existing one.
    """
    subscription, plan = get_active_plan(db, restaurant_id)
    limit = plan["max_admin_users"]
    current_count = (
        db.query(models.AdminRestaurantAccess)
        .filter(models.AdminRestaurantAccess.restaurant_id == restaurant_id)
        .count()
    )
    if current_count < limit:
        return

    db.add(models.SubscriptionEvent(
        restaurant_id=restaurant_id,
        event_type="admin_user_quota_exceeded",
        payload=json.dumps({
            "plan_code": subscription.plan_code, "limit": limit, "current_count": current_count,
        }),
    ))
    db.commit()
    raise HTTPException(
        status_code=status.HTTP_402_PAYMENT_REQUIRED,
        detail={
            "error_code": "admin_user_quota_exceeded",
            "message": f"This restaurant already has the maximum number of admin users "
                       f"({limit}) allowed on the {plan['display_name']} plan.",
        },
    )


def check_whatsapp_allowed(db: Session, restaurant_id: int) -> None:
    """
    Blocks WhatsApp on a plan where whatsapp_enabled is False (Product
    Decision #5: Starter today, but driven by the plan catalog, not a
    hardcoded plan_code check). Used both to gate CREATING/upserting a
    mapping (routers/platform_admin.py:set_whatsapp_number — deleting a
    mapping is deliberately never gated, downgrade never deletes it) and
    to gate PROCESSING an inbound message on an existing mapping
    (app/whatsapp_processing.py) — so a restaurant that downgrades off a
    WhatsApp-enabled plan stops receiving AI replies on that channel even
    though its WhatsAppNumber row is left in place untouched.
    """
    subscription, plan = get_active_plan(db, restaurant_id)
    if plan["whatsapp_enabled"]:
        return

    db.add(models.SubscriptionEvent(
        restaurant_id=restaurant_id,
        event_type="whatsapp_blocked_plan_not_enabled",
        payload=json.dumps({"plan_code": subscription.plan_code}),
    ))
    db.commit()
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail={
            "error_code": "whatsapp_not_enabled_for_plan",
            "message": f"WhatsApp is not available on the {plan['display_name']} plan.",
        },
    )


def record_subscription_change_event(
    db: Session, subscription: models.Subscription, *, old_plan_code: str, old_status: str
) -> None:
    """
    Writes one SubscriptionEvent when a superadmin's PATCH actually
    changes plan_code and/or status (routers/platform_admin.py:
    update_restaurant_subscription). Call this AFTER applying the
    update's setattr()s to `subscription` but BEFORE that function's own
    db.commit() — it rides along in the same transaction, so the event
    and the subscription change are never persisted independently of
    each other. A PATCH that changes neither field (values resubmitted
    unchanged) writes no event.
    """
    if subscription.plan_code == old_plan_code and subscription.status == old_status:
        return
    db.add(models.SubscriptionEvent(
        restaurant_id=subscription.restaurant_id,
        event_type="plan_or_status_changed",
        payload=json.dumps({
            "old_plan_code": old_plan_code,
            "new_plan_code": subscription.plan_code,
            "old_status": old_status,
            "new_status": subscription.status,
        }),
    ))
