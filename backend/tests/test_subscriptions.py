"""
Jantar SaaS Phase 1: schema + plan definitions only (see app/plans.py
and app/models.py:Subscription/SubscriptionEvent, and the
7459207d9901_subscriptions migration's docstring).

Nothing here tests enforcement, billing, checkout, or any admin/API
surface for subscriptions -- none of that exists yet. This file only
verifies: the migration backfills exactly one Subscription row per
restaurant that already existed in the table AT MIGRATION TIME, with
the documented defaults; restaurant_id is enforced unique (one
subscription per restaurant); the plan catalog matches the approved
final numbers; and that adding this table changed nothing about
existing restaurant data. Note: in THIS test suite, the demo restaurant
(app/seed_data.py) and any restaurant created via the onboarding
endpoint are both created strictly AFTER migrations run against a fresh
test database, so neither ever gets backfilled here either -- see
test_restaurants_created_after_migration_time_have_no_subscription_yet.
That is the same, deliberately untouched gap the migration's docstring
describes for a real, already-populated (e.g. production) database.
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
    restaurant 1, so this test's own committed row can't affect any
    other test in this file regardless of execution order."""
    db.add(models.Subscription(restaurant_id=second_restaurant, plan_code="starter", status="active"))
    db.commit()

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


def test_restaurants_created_after_migration_time_have_no_subscription_yet(client, admin_headers, second_restaurant, db):
    """
    Documents the deliberate Phase 1 scope boundary: this migration only
    backfills restaurants that already existed in the table AT
    MIGRATION TIME (see
    test_migration_backfills_one_subscription_per_pre_existing_restaurant,
    which proves that case in isolation against a real pre-existing
    row). Neither the demo restaurant seeded by app/seed_data.py (which
    runs on every fresh test database strictly AFTER migrations,
    including this one) nor a restaurant created afterwards through the
    ordinary onboarding endpoint gets a Subscription row automatically
    -- that wiring is a separately-tracked later phase, not something
    this migration or Phase 1 attempts. This test exists so that gap
    stays a documented, intentional decision rather than silently
    changing (in either direction) without a deliberate choice.
    """
    for restaurant_id in (1, second_restaurant):
        subscription = (
            db.query(models.Subscription)
            .filter(models.Subscription.restaurant_id == restaurant_id)
            .first()
        )
        assert subscription is None, f"restaurant {restaurant_id} unexpectedly has a subscription"


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
