"""
Jantar SaaS Phase 1: schema + plan definitions only (see app/plans.py
and app/models.py:Subscription/SubscriptionEvent, and the
7459207d9901_subscriptions migration's docstring).

Nothing here tests enforcement, billing, checkout, or any admin/API
surface for subscriptions -- none of that exists yet (see
tests/test_subscription_visibility.py for the Phase 2 visibility
endpoints and onboarding-initialization behavior). This file only
verifies: the migration backfills exactly one Subscription row per
restaurant that already existed in the table AT MIGRATION TIME, with
the documented defaults; restaurant_id is enforced unique (one
subscription per restaurant); the plan catalog matches the approved
final numbers; and that adding this table changed nothing about
existing restaurant data.

Historical note: Phase 1 left restaurants created AFTER the migration
(the demo restaurant seeded by app/seed_data.py, and any restaurant
onboarded via the API) without a Subscription row -- a documented,
deliberate gap at the time. Phase 2 closed that gap (both call sites
now create one immediately), so that is no longer true here; see
tests/test_subscription_visibility.py for the current behavior.
"""

import sqlite3
from pathlib import Path

import pytest
from sqlalchemy.exc import IntegrityError

from app import config as app_config
from app import models, plans

_ALEMBIC_INI = Path(__file__).resolve().parent.parent / "alembic.ini"


def test_restaurant_id_is_unique_on_subscriptions(db, second_restaurant):
    """The core one-subscription-per-restaurant invariant: a second row
    for an already-subscribed restaurant must be rejected at the DB
    level, not just by application convention. Uses a freshly-created
    restaurant (via the second_restaurant fixture) rather than
    restaurant 1, so this test's own attempted row can't affect any
    other test in this file regardless of execution order. Since Phase
    2, second_restaurant already has its automatically-created
    subscription by the time this fixture returns (see
    tests/test_subscription_visibility.py), so only ONE additional
    insert is attempted here, not two."""
    db.add(models.Subscription(restaurant_id=second_restaurant, plan_code="growth", status="active"))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_migration_backfills_one_subscription_per_pre_existing_restaurant(tmp_path, monkeypatch):
    """
    Runs the migration chain against a throwaway SQLite file, seeding
    restaurants BEFORE the subscriptions migration runs, to verify the
    backfill in true isolation -- independent of whatever restaurants
    the shared test-session database (used by every other test in this
    file, via the `db`/`client` fixtures) happens to contain by the time
    this test runs, and independent of test execution order.

    alembic/env.py always takes its DB URL from app.config.DATABASE_URL
    (a single shared module attribute, not an argument), so that
    attribute is monkeypatched for the duration of this test rather than
    passed to alembic directly.
    """
    from alembic import command
    from alembic.config import Config

    db_path = tmp_path / "migration_backfill_check.db"
    monkeypatch.setattr(app_config, "DATABASE_URL", f"sqlite:///{db_path}")
    cfg = Config(str(_ALEMBIC_INI))

    command.upgrade(cfg, "e57da8eef647")  # every migration EXCEPT subscriptions

    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT INTO restaurants (name, address, phone, email, seating_capacity) "
        "VALUES ('Backfill Test Restaurant', 'Addr', 'Phone', 'e@example.com', 40)"
    )
    conn.commit()
    conn.close()

    command.upgrade(cfg, "head")  # now applies the subscriptions migration

    conn = sqlite3.connect(str(db_path))
    rows = conn.execute(
        "SELECT plan_code, status, billing_provider, provider_customer_id, "
        "provider_subscription_id, current_period_end, cancel_at_period_end FROM subscriptions"
    ).fetchall()
    conn.close()

    assert rows == [("starter", "active", None, None, None, None, 0)]


# Note: a test previously lived here documenting that a restaurant
# created after this migration ran (the seeded demo restaurant, or one
# onboarded via the API) got no Subscription row -- the Phase 1 gap
# this migration's docstring describes. Phase 2 closed that gap (see
# app/subscriptions.py:create_subscription_for_restaurant and its two
# call sites); the current behavior is covered by
# tests/test_subscription_visibility.py instead.


def test_existing_restaurant_row_is_bit_for_bit_unchanged_by_the_migration(tmp_path, monkeypatch):
    """
    The Restaurant table itself gained no column and no altered value --
    Subscription is a wholly separate, additive table. Checked in the
    same true isolation as
    test_migration_backfills_one_subscription_per_pre_existing_restaurant
    (a fresh throwaway database, not the shared test-session `db`
    fixture) because that database is shared across the WHOLE suite and
    other test files legitimately mutate restaurant 1's profile fields
    and child rows as part of their own scenarios -- this test needs a
    restaurant only this test can touch, to prove the migration itself
    changed nothing about it.
    """
    from alembic import command
    from alembic.config import Config

    db_path = tmp_path / "restaurant_unchanged_check.db"
    monkeypatch.setattr(app_config, "DATABASE_URL", f"sqlite:///{db_path}")
    cfg = Config(str(_ALEMBIC_INI))

    command.upgrade(cfg, "e57da8eef647")

    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT INTO restaurants (name, address, phone, email, map_link, parking_notes, seating_capacity) "
        "VALUES ('Isolated Test Restaurant', '1 Test Street', '01234 000000', 'e@example.com', "
        "'https://maps.example.com/x', 'Park round the back', 55)"
    )
    conn.commit()
    before = conn.execute("SELECT * FROM restaurants WHERE id = 1").fetchone()
    conn.close()

    command.upgrade(cfg, "head")

    conn = sqlite3.connect(str(db_path))
    after = conn.execute("SELECT * FROM restaurants WHERE id = 1").fetchone()
    conn.close()

    assert after == before


# --- Plan catalog (app/plans.py) ---

def test_plan_codes_match_the_approved_three_plans():
    assert plans.PLAN_CODES == ("starter", "growth", "pro")
    assert set(plans.PLAN_LIMITS.keys()) == {"starter", "growth", "pro"}
    assert plans.DEFAULT_PLAN_CODE == "starter"


@pytest.mark.parametrize(
    "plan_code, expected",
    [
        (
            "starter",
            {
                "display_name": "Starter",
                "monthly_price_usd": 39,
                "max_conversations_per_month": 300,
                "max_admin_users": 1,
                "whatsapp_enabled": False,
                "custom_widget_branding": "basic",
            },
        ),
        (
            "growth",
            {
                "display_name": "Growth",
                "monthly_price_usd": 79,
                "max_conversations_per_month": 1000,
                "max_admin_users": 3,
                "whatsapp_enabled": True,
                "custom_widget_branding": "full",
            },
        ),
        (
            "pro",
            {
                "display_name": "Pro",
                "monthly_price_usd": 149,
                "max_conversations_per_month": 3000,
                "max_admin_users": 5,
                "whatsapp_enabled": True,
                "custom_widget_branding": "full",
            },
        ),
    ],
)
def test_plan_definition_matches_the_approved_final_numbers(plan_code, expected):
    assert plans.PLAN_LIMITS[plan_code] == expected
