"""
Superadmin restaurant onboarding readiness checklist (read-only).

Computes, for one restaurant, a read-only summary of how ready it is
for real customer traffic -- entirely from data that already exists
(Restaurant, MenuItem, OpeningHours, FAQ, WidgetConfig/WidgetAllowedOrigin,
WhatsAppNumber, Subscription). No new table, no new business rule, and
nothing here enforces or blocks anything at the API level -- it only
reports what a superadmin would otherwise have to check tab by tab.

Each check is either BLOCKING (counts toward overall_status below) or
purely INFORMATIONAL (shown, but never affects overall_status):

  - profile        BLOCKING   -- name/address/phone/email/seating
                                 capacity/map link all present.
  - menu           BLOCKING   -- at least one MenuItem exists.
  - opening_hours  BLOCKING   -- all 7 canonical days have a row, and
                                 each is "usable" (either marked closed,
                                 or has both an open_time and a
                                 close_time) -- the same definition
                                 app/knowledge.py already uses when
                                 building the chatbot's context.
  - widget         BLOCKING   -- WidgetConfig exists, is_active, and
                                 has at least one WidgetAllowedOrigin.
                                 A restaurant can't actually serve a
                                 customer through the widget without
                                 all three, even though none of this is
                                 enforced anywhere else.
  - faqs           informational -- FAQs are optional in this
                                 architecture (nothing else requires
                                 one), so this is labeled "optional",
                                 never "needs setup", and never blocks.
  - whatsapp       informational -- plan-gated (app/plans.py's
                                 whatsapp_enabled) and, even on a plan
                                 that includes it, connecting a real
                                 Meta phone number is an external,
                                 credential-gated step outside what an
                                 onboarding checklist should force -- so
                                 this never blocks either way.
  - subscription   informational -- plan/status/usage-vs-limit display
                                 only. app/plans.py's own docstring says
                                 nothing enforces these limits yet, and
                                 this checklist doesn't start; flagged
                                 with a warning icon only when usage has
                                 reached the plan limit or the
                                 subscription's status itself needs
                                 attention (past_due/canceled).

overall_status (three-way split; the task only named the three labels,
not the exact rule, so this is documented here rather than left
implicit):
  - "critical_setup_missing": the restaurant PROFILE itself is
    incomplete -- without basic contact details nothing else can be
    meaningfully verified or served.
  - "incomplete": profile is fine, but at least one other blocking
    check (menu, opening hours, widget) still needs setup.
  - "ready": every blocking check is complete.
"""

from sqlalchemy.orm import Session

from . import models
from .plans import DEFAULT_PLAN_CODE, PLAN_LIMITS
from .subscriptions import current_period_conversation_count

_CANONICAL_DAYS = (
    "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday",
)


def _check_profile(restaurant: models.Restaurant) -> dict:
    missing = []
    if not restaurant.name:
        missing.append("name")
    if not restaurant.address:
        missing.append("address")
    if not restaurant.phone:
        missing.append("phone")
    if not restaurant.email:
        missing.append("email")
    if not restaurant.seating_capacity:
        missing.append("seating capacity")
    if not restaurant.map_link:
        missing.append("map link")

    if not missing:
        return {
            "key": "profile", "label": "Restaurant profile", "status": "complete",
            "blocking": True, "detail": "All required restaurant details are present.",
            "next_actions": [],
        }
    return {
        "key": "profile", "label": "Restaurant profile", "status": "needs_setup",
        "blocking": True, "detail": f"Missing: {', '.join(missing)}.",
        "next_actions": ["Complete restaurant profile"],
    }


def _check_menu(db: Session, restaurant_id: int) -> dict:
    count = db.query(models.MenuItem).filter(models.MenuItem.restaurant_id == restaurant_id).count()
    if count > 0:
        return {
            "key": "menu", "label": "Menu", "status": "complete", "blocking": True,
            "detail": f"{count} menu item(s).", "next_actions": [],
        }
    return {
        "key": "menu", "label": "Menu", "status": "needs_setup", "blocking": True,
        "detail": "No menu items yet.", "next_actions": ["Add menu items"],
    }


def _check_opening_hours(db: Session, restaurant_id: int) -> dict:
    rows = (
        db.query(models.OpeningHours)
        .filter(models.OpeningHours.restaurant_id == restaurant_id)
        .all()
    )
    by_day = {row.day_of_week: row for row in rows}
    usable_days = sum(
        1 for day in _CANONICAL_DAYS
        if (row := by_day.get(day)) and (row.is_closed or (row.open_time and row.close_time))
    )

    if usable_days == len(_CANONICAL_DAYS):
        return {
            "key": "opening_hours", "label": "Opening hours", "status": "complete", "blocking": True,
            "detail": "All 7 days configured.", "next_actions": [],
        }
    return {
        "key": "opening_hours", "label": "Opening hours", "status": "needs_setup", "blocking": True,
        "detail": f"{usable_days} of 7 days configured with usable hours.",
        "next_actions": ["Configure opening hours"],
    }


def _check_faqs(db: Session, restaurant_id: int) -> dict:
    count = db.query(models.FAQ).filter(models.FAQ.restaurant_id == restaurant_id).count()
    if count > 0:
        return {
            "key": "faqs", "label": "FAQs", "status": "complete", "blocking": False,
            "detail": f"{count} FAQ(s).", "next_actions": [],
        }
    return {
        "key": "faqs", "label": "FAQs", "status": "optional_not_configured", "blocking": False,
        "detail": "No FAQs yet (optional).", "next_actions": ["Add FAQs (optional)"],
    }


def _check_widget(db: Session, restaurant_id: int) -> dict:
    widget = (
        db.query(models.WidgetConfig)
        .filter(models.WidgetConfig.restaurant_id == restaurant_id)
        .first()
    )
    if not widget:
        return {
            "key": "widget", "label": "Widget configuration", "status": "needs_setup", "blocking": True,
            "detail": "No widget configuration exists yet.",
            "next_actions": ["Create widget configuration"],
        }

    origin_count = len(widget.allowed_origins)
    next_actions = []
    if not widget.is_active:
        next_actions.append("Activate the widget when ready")
    if origin_count == 0:
        next_actions.append("Add website origin")

    if not next_actions:
        return {
            "key": "widget", "label": "Widget configuration", "status": "complete", "blocking": True,
            "detail": f"Active, {origin_count} allowed origin(s).", "next_actions": [],
        }

    detail = f"{'Active' if widget.is_active else 'Inactive'}, {origin_count} allowed origin(s)."
    if origin_count == 0:
        detail += " Website origin not configured."
    return {
        "key": "widget", "label": "Widget configuration", "status": "needs_setup", "blocking": True,
        "detail": detail, "next_actions": next_actions,
    }


def _check_whatsapp(db: Session, restaurant_id: int, plan_code: str) -> dict:
    plan = PLAN_LIMITS.get(plan_code)
    whatsapp_enabled_on_plan = bool(plan and plan["whatsapp_enabled"])

    mapping = (
        db.query(models.WhatsAppNumber)
        .filter(models.WhatsAppNumber.restaurant_id == restaurant_id)
        .first()
    )
    if mapping:
        return {
            "key": "whatsapp", "label": "WhatsApp", "status": "complete", "blocking": False,
            "detail": f"Connected ({mapping.display_phone_number}).", "next_actions": [],
        }

    if not whatsapp_enabled_on_plan:
        return {
            "key": "whatsapp", "label": "WhatsApp", "status": "optional_not_configured", "blocking": False,
            "detail": "Not included in the current plan.", "next_actions": [],
        }

    return {
        "key": "whatsapp", "label": "WhatsApp", "status": "needs_setup", "blocking": False,
        "detail": "Not connected yet.", "next_actions": ["Connect WhatsApp"],
    }


def _check_subscription(db: Session, restaurant_id: int, subscription: models.Subscription | None) -> dict:
    if not subscription:
        return {
            "key": "subscription", "label": "Subscription", "status": "needs_setup", "blocking": False,
            "detail": "No subscription record exists yet.", "next_actions": [],
        }

    plan = PLAN_LIMITS.get(subscription.plan_code)
    plan_name = plan["display_name"] if plan else subscription.plan_code
    limit = plan["max_conversations_per_month"] if plan else None
    usage = current_period_conversation_count(db, restaurant_id)

    over_limit = limit is not None and usage >= limit
    unhealthy_status = subscription.status in ("past_due", "canceled")

    detail = f"{plan_name} plan, status: {subscription.status}. Usage this period: {usage}"
    detail += f" / {limit}." if limit is not None else "."

    return {
        "key": "subscription", "label": "Subscription",
        "status": "needs_setup" if (over_limit or unhealthy_status) else "complete",
        "blocking": False, "detail": detail, "next_actions": [],
    }


def compute_restaurant_onboarding_status(db: Session, restaurant: models.Restaurant) -> dict:
    subscription = (
        db.query(models.Subscription)
        .filter(models.Subscription.restaurant_id == restaurant.id)
        .first()
    )
    plan_code = subscription.plan_code if subscription else DEFAULT_PLAN_CODE

    checks = [
        _check_profile(restaurant),
        _check_menu(db, restaurant.id),
        _check_opening_hours(db, restaurant.id),
        _check_faqs(db, restaurant.id),
        _check_widget(db, restaurant.id),
        _check_whatsapp(db, restaurant.id, plan_code),
        _check_subscription(db, restaurant.id, subscription),
    ]

    profile_check = next(c for c in checks if c["key"] == "profile")
    blocking_checks = [c for c in checks if c["blocking"]]

    if profile_check["status"] != "complete":
        overall_status = "critical_setup_missing"
    elif all(c["status"] == "complete" for c in blocking_checks):
        overall_status = "ready"
    else:
        overall_status = "incomplete"

    return {
        "restaurant_id": restaurant.id,
        "restaurant_name": restaurant.name,
        "overall_status": overall_status,
        "checks": checks,
    }
