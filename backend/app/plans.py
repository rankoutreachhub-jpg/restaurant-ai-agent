"""
Subscription plan catalog (Jantar SaaS Phase 1 — schema + definitions
only, see app/models.py:Subscription).

This is the one place plan names/prices/limits are defined, mirroring
app/config.py's role as the single source of truth for environment
settings. A real Plan database table would be needed if pricing ever
had to change without a deploy, but for three fixed plans that is not
justified yet — changing a limit here is a one-line code change,
exactly like GEMINI_MODEL_NAME's existing tradeoff.

Nothing in the application reads PLAN_LIMITS yet. No endpoint, admin UI,
or usage check enforces any of this in Phase 1 — it exists so a later
phase has one source of truth to enforce against, rather than each
enforcement point inventing its own numbers.
"""

from typing import Literal, TypedDict

PlanCode = Literal["starter", "growth", "pro"]

PLAN_CODES: tuple[PlanCode, ...] = ("starter", "growth", "pro")

# The plan every restaurant is backfilled onto (see the Phase 1
# migration) and the default for any restaurant that doesn't yet have
# an explicit plan assignment.
DEFAULT_PLAN_CODE: PlanCode = "starter"


class PlanDefinition(TypedDict):
    display_name: str
    monthly_price_usd: int
    max_conversations_per_month: int
    max_admin_users: int
    whatsapp_enabled: bool
    custom_widget_branding: Literal["basic", "full"]


PLAN_LIMITS: dict[PlanCode, PlanDefinition] = {
    "starter": {
        "display_name": "Starter",
        "monthly_price_usd": 39,
        "max_conversations_per_month": 300,
        "max_admin_users": 1,
        "whatsapp_enabled": False,
        "custom_widget_branding": "basic",
    },
    "growth": {
        "display_name": "Growth",
        "monthly_price_usd": 79,
        "max_conversations_per_month": 1000,
        "max_admin_users": 3,
        "whatsapp_enabled": True,
        "custom_widget_branding": "full",
    },
    "pro": {
        "display_name": "Pro",
        "monthly_price_usd": 149,
        "max_conversations_per_month": 3000,
        "max_admin_users": 5,
        "whatsapp_enabled": True,
        "custom_widget_branding": "full",
    },
}
