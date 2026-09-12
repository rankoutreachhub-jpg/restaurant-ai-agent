"""
Onboarding hardening: every restaurant must get exactly one WidgetConfig
row from the moment it's created (mirroring Jantar SaaS Phase 2's
Subscription treatment) -- see routers/platform_admin.py:create_restaurant.

This file covers the guarantees that change adds:
- a freshly created restaurant already has exactly one WidgetConfig
- Restaurant + Subscription + WidgetConfig are committed atomically
- a failure before that commit leaves no partial records at all
- an already-existing restaurant is never given a second WidgetConfig
- no WidgetAllowedOrigin row is ever auto-created

Everyday widget-configuration behavior itself (create/update/read via the
admin endpoint) is unchanged and stays covered by
tests/test_widget_config_admin.py.
"""

import pytest

from app import models
from app.database import SessionLocal


def _create_restaurant(client, admin_headers, name="The Ferry Inn Onboarding Test", email=None):
    return client.post(
        "/admin/platform/restaurants",
        json={
            "name": name,
            "address": "2 River Lane",
            "phone": "01000 000000",
            "email": email or "hello@onboarding-test.example.com",
        },
        headers=admin_headers,
    )


def test_new_restaurant_gets_exactly_one_widget_config(client, admin_headers, db):
    response = _create_restaurant(client, admin_headers)
    assert response.status_code == 201
    restaurant_id = response.json()["restaurant"]["id"]

    configs = db.query(models.WidgetConfig).filter(
        models.WidgetConfig.restaurant_id == restaurant_id
    ).all()
    assert len(configs) == 1
    assert configs[0].is_active is False
    assert configs[0].primary_language == "en-GB"
    assert configs[0].booking_enabled is True
    assert configs[0].welcome_message is None
    assert configs[0].logo_url is None
    assert configs[0].accent_color is None
    assert isinstance(configs[0].widget_key, str) and configs[0].widget_key.startswith("wgt_")


def test_new_restaurant_gets_restaurant_subscription_and_widget_config_atomically(
    client, admin_headers, db
):
    response = _create_restaurant(client, admin_headers, name="Atomic Creation Test")
    assert response.status_code == 201
    restaurant_id = response.json()["restaurant"]["id"]

    assert db.query(models.Restaurant).filter(models.Restaurant.id == restaurant_id).count() == 1
    assert db.query(models.Subscription).filter(
        models.Subscription.restaurant_id == restaurant_id
    ).count() == 1
    assert db.query(models.WidgetConfig).filter(
        models.WidgetConfig.restaurant_id == restaurant_id
    ).count() == 1


def test_failed_creation_leaves_no_partial_restaurant_subscription_or_widget_config(
    client, admin_headers, monkeypatch
):
    """
    Simulates a failure between the Restaurant flush() and the final
    commit() (here: widget_key generation blowing up) -- since nothing
    in that transaction has been committed yet, the whole thing must
    roll back together, exactly like the Subscription-only atomicity
    this same pattern already relied on.
    """
    from app.routers import platform_admin

    def _boom():
        raise RuntimeError("simulated widget_key generation failure")

    session = SessionLocal()
    try:
        restaurants_before = session.query(models.Restaurant).count()
        subscriptions_before = session.query(models.Subscription).count()
        widget_configs_before = session.query(models.WidgetConfig).count()
    finally:
        session.close()

    monkeypatch.setattr(platform_admin, "generate_widget_key", _boom)

    with pytest.raises(RuntimeError):
        _create_restaurant(client, admin_headers, name="Should Never Exist")

    session = SessionLocal()
    try:
        assert session.query(models.Restaurant).filter(
            models.Restaurant.name == "Should Never Exist"
        ).count() == 0
        assert session.query(models.Restaurant).count() == restaurants_before
        assert session.query(models.Subscription).count() == subscriptions_before
        assert session.query(models.WidgetConfig).count() == widget_configs_before
    finally:
        session.close()


def test_existing_restaurant_does_not_get_a_duplicate_widget_config(
    client, admin_headers, second_restaurant, db
):
    """
    second_restaurant was created (via the same endpoint under test)
    once already by its fixture -- fetching it again, or performing any
    other read of it, must never conjure up a second WidgetConfig row.
    """
    configs_before = db.query(models.WidgetConfig).filter(
        models.WidgetConfig.restaurant_id == second_restaurant
    ).count()
    assert configs_before == 1

    get_response = client.get("/admin/platform/restaurants", headers=admin_headers)
    assert get_response.status_code == 200

    configs_after = db.query(models.WidgetConfig).filter(
        models.WidgetConfig.restaurant_id == second_restaurant
    ).count()
    assert configs_after == 1


def test_restaurant_id_is_unique_on_widget_configs(db, second_restaurant):
    """
    Defense in depth, mirroring test_subscriptions.py's namesake for
    Subscription: even if application code ever forgot to check before
    inserting, the database itself must refuse a second WidgetConfig row
    for the same restaurant_id.
    """
    from sqlalchemy.exc import IntegrityError
    from app.widget_keys import generate_widget_key

    db.add(models.WidgetConfig(restaurant_id=second_restaurant, widget_key=generate_widget_key()))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_new_restaurant_gets_no_widget_allowed_origin_automatically(client, admin_headers, db):
    """
    Requirement: WidgetAllowedOrigin is never auto-created. The
    restaurant's real website origin must remain an explicit,
    separately-performed configuration step (POST .../widget-config/origins).
    """
    response = _create_restaurant(client, admin_headers, name="No Auto Origin Test")
    assert response.status_code == 201
    restaurant_id = response.json()["restaurant"]["id"]

    config = db.query(models.WidgetConfig).filter(
        models.WidgetConfig.restaurant_id == restaurant_id
    ).one()
    origins = db.query(models.WidgetAllowedOrigin).filter(
        models.WidgetAllowedOrigin.widget_config_id == config.id
    ).all()
    assert origins == []
