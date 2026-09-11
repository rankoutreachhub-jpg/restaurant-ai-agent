"""
Subscription helpers (Jantar SaaS Phase 2 — visibility only; see
app/plans.py for the plan catalog and app/models.py:Subscription for the
schema, both added in Phase 1).

Centralizes the one thing that creates a Subscription row, mirroring
app/conversations.py's and app/booking.py's "one function, one place
that writes this" convention — the two places a Restaurant is ever
constructed (routers/platform_admin.py's create_restaurant and
seed_data.py's seed_if_empty) both call create_subscription_for_restaurant
immediately after, closing Phase 1's documented gap where a
restaurant created after that migration had no subscription row.

Also holds the read-only helpers the new visibility endpoints need:
current-calendar-month usage and the lifecycle status vocabulary.
Nothing here enforces or blocks anything — see app/plans.py's own
docstring for why enforcement is a later phase.
"""

from datetime import datetime
from typing import Literal

from sqlalchemy.orm import Session

from . import models
from .plans import DEFAULT_PLAN_CODE

# The subscription lifecycle states this system recognises (see
# models.Subscription's docstring) — enforced at the application layer
# only, like Booking.status/Conversation.channel, not a DB CHECK
# constraint, so a new allowed value never needs a migration.
SUBSCRIPTION_STATUSES: tuple[str, ...] = ("trialing", "active", "past_due", "canceled")
SubscriptionStatus = Literal["trialing", "active", "past_due", "canceled"]


def create_subscription_for_restaurant(
    db: Session, restaurant: models.Restaurant, *, commit: bool = True
) -> models.Subscription:
    """
    Creates the one Subscription row a restaurant must have. Callers
    always call this immediately after inserting a brand-new
    restaurant, so restaurant_id is guaranteed not to already have
    one — the table's own unique constraint on restaurant_id is the
    backstop if that assumption is ever wrong, turning a would-be
    duplicate into a clear IntegrityError rather than a silent
    second row.
    """
    subscription = models.Subscription(
        restaurant_id=restaurant.id,
        plan_code=DEFAULT_PLAN_CODE,
        status="active",
        billing_provider=None,
        provider_customer_id=None,
        provider_subscription_id=None,
        current_period_end=None,
        cancel_at_period_end=False,
    )
    db.add(subscription)
    if commit:
        db.commit()
        db.refresh(subscription)
    return subscription


def current_period_conversation_count(db: Session, restaurant_id: int) -> int:
    """
    AI conversations started this calendar month — the only usage
    dimension Phase 2 surfaces. Reuses the existing Conversation table
    directly; no new live usage-counter table.
    """
    now = datetime.utcnow()
    period_start = datetime(now.year, now.month, 1)
    return (
        db.query(models.Conversation)
        .filter(
            models.Conversation.restaurant_id == restaurant_id,
            models.Conversation.created_at >= period_start,
        )
        .count()
    )
