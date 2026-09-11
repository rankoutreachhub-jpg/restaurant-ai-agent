"""
Jantar SaaS Phase 2: subscription visibility and safe restaurant
onboarding initialization (see app/subscriptions.py, the new
GET/PATCH endpoints in routers/admin.py and routers/platform_admin.py,
and the additive fields on GET /admin/platform/restaurants).

Still no payment provider, checkout, or usage enforcement anywhere in
this file — only that every restaurant automatically gets exactly one
Subscription row, that the new endpoints respect the existing
restaurant-access/superadmin auth patterns unchanged, that invalid
plan/status values are rejected, and that the usage counter reflects
real Conversation rows for the current calendar month.
"""

from datetime import datetime, timedelta

from app import models


def _subscription_url(restaurant_id):
    return f"/admin/restaurant/{restaurant_id}/subscription"


def _platform_subscription_url(restaurant_id):
    return f"/admin/platform/restaurants/{restaurant_id}/subscription"


# --- Automatic subscription creation on restaurant onboarding ---

def test_new_restaurant_automatically_gets_one_starter_active_subscription(
    client, admin_headers, second_restaurant, db
):
    subscription = (
        db.query(models.Subscription)
        .filter(models.Subscription.restaurant_id == second_restaurant)
        .one()
    )
    assert subscription.plan_code == "starter"
    assert subscription.status == "active"
    assert subscription.billing_provider is None
    assert subscription.provider_customer_id is None
    assert subscription.provider_subscription_id is None
    assert subscription.current_period_end is None
    assert subscription.cancel_at_period_end is False


def test_new_restaurant_gets_exactly_one_subscription_row_not_more(
    client, admin_headers, second_restaurant, db
):
    rows = (
        db.query(models.Subscription)
        .filter(models.Subscription.restaurant_id == second_restaurant)
        .all()
    )
    assert len(rows) == 1


def test_onboarding_two_restaurants_gives_each_its_own_subscription(client, admin_headers, db):
    r1 = client.post(
        "/admin/platform/restaurants",
        json={
            "name": "Onboard Test A", "address": "A", "phone": "1",
            "email": "a@example.com", "seating_capacity": 20,
        },
        headers=admin_headers,
    ).json()["restaurant"]["id"]
    r2 = client.post(
        "/admin/platform/restaurants",
        json={
            "name": "Onboard Test B", "address": "B", "phone": "2",
            "email": "b@example.com", "seating_capacity": 20,
        },
        headers=admin_headers,
    ).json()["restaurant"]["id"]

    assert r1 != r2
    for restaurant_id in (r1, r2):
        rows = db.query(models.Subscription).filter(models.Subscription.restaurant_id == restaurant_id).all()
        assert len(rows) == 1
        assert rows[0].plan_code == "starter"
        assert rows[0].status == "active"


# --- Restaurant-scoped GET /admin/restaurant/{id}/subscription ---

def test_get_subscription_requires_a_key_at_all(client):
    response = client.get(_subscription_url(1))
    assert response.status_code == 401


def test_scoped_admin_cannot_view_a_restaurant_it_is_not_granted(client, second_restaurant, scoped_admin_key):
    _, headers = scoped_admin_key([1])  # NOT granted second_restaurant
    response = client.get(_subscription_url(second_restaurant), headers=headers)
    assert response.status_code == 404


def test_scoped_admin_can_view_its_own_granted_restaurant_subscription(client, scoped_admin_key):
    _, headers = scoped_admin_key([1])
    response = client.get(_subscription_url(1), headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["restaurant_id"] == 1
    assert body["plan_code"] == "starter"
    assert body["plan_name"] == "Starter"
    assert body["status"] == "active"
    assert body["cancel_at_period_end"] is False
    assert body["current_period_end"] is None
    assert "usage" in body and "conversations_this_period" in body["usage"]
    assert body["limits"]["max_conversations_per_month"] == 300
    assert body["limits"]["max_admin_users"] == 1
    assert body["limits"]["whatsapp_enabled"] is False
    assert body["limits"]["custom_widget_branding"] == "basic"
    assert body["limits"]["monthly_price_usd"] == 39


def test_superadmin_can_view_any_restaurant_subscription(client, admin_headers, second_restaurant):
    response = client.get(_subscription_url(second_restaurant), headers=admin_headers)
    assert response.status_code == 200
    assert response.json()["restaurant_id"] == second_restaurant


def test_unknown_restaurant_subscription_returns_404(client, admin_headers):
    response = client.get(_subscription_url(999999), headers=admin_headers)
    assert response.status_code == 404


# --- Platform-admin GET/PATCH .../platform/restaurants/{id}/subscription ---

def test_platform_get_subscription_requires_a_key_at_all(client):
    response = client.get(_platform_subscription_url(1))
    assert response.status_code == 401


def test_platform_get_subscription_requires_superadmin(client, scoped_admin_key):
    _, headers = scoped_admin_key([1])
    response = client.get(_platform_subscription_url(1), headers=headers)
    assert response.status_code == 403


def test_platform_patch_subscription_requires_superadmin(client, scoped_admin_key):
    _, headers = scoped_admin_key([1])
    response = client.patch(_platform_subscription_url(1), json={"plan_code": "growth"}, headers=headers)
    assert response.status_code == 403


def test_superadmin_can_view_full_subscription_details(client, admin_headers, second_restaurant):
    response = client.get(_platform_subscription_url(second_restaurant), headers=admin_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["restaurant_id"] == second_restaurant
    assert body["plan_code"] == "starter"
    assert body["status"] == "active"
    assert body["billing_provider"] is None
    assert "created_at" in body


def test_superadmin_can_change_plan_and_status(client, admin_headers, second_restaurant, db):
    response = client.patch(
        _platform_subscription_url(second_restaurant),
        json={"plan_code": "growth", "status": "past_due"},
        headers=admin_headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["plan_code"] == "growth"
    assert body["status"] == "past_due"

    db.expire_all()
    subscription = (
        db.query(models.Subscription)
        .filter(models.Subscription.restaurant_id == second_restaurant)
        .one()
    )
    assert subscription.plan_code == "growth"
    assert subscription.status == "past_due"


def test_patch_only_updates_the_fields_provided(client, admin_headers, second_restaurant):
    client.patch(_platform_subscription_url(second_restaurant), json={"plan_code": "pro"}, headers=admin_headers)
    response = client.patch(_platform_subscription_url(second_restaurant), json={"status": "canceled"}, headers=admin_headers)
    assert response.status_code == 200
    body = response.json()
    # plan_code from the FIRST patch must survive the second, status-only patch.
    assert body["plan_code"] == "pro"
    assert body["status"] == "canceled"


def test_invalid_plan_code_is_rejected(client, admin_headers, second_restaurant):
    response = client.patch(
        _platform_subscription_url(second_restaurant),
        json={"plan_code": "enterprise-deluxe"},
        headers=admin_headers,
    )
    assert response.status_code == 422


def test_invalid_status_is_rejected(client, admin_headers, second_restaurant):
    response = client.patch(
        _platform_subscription_url(second_restaurant),
        json={"status": "definitely_not_a_real_status"},
        headers=admin_headers,
    )
    assert response.status_code == 422


def test_patch_does_not_expose_provider_fields(client, admin_headers, second_restaurant):
    """billing_provider/provider_customer_id/provider_subscription_id are
    not accepted by this endpoint at all -- extra fields are silently
    ignored by FastAPI/Pydantic (not a validation error), but they must
    never actually be written."""
    response = client.patch(
        _platform_subscription_url(second_restaurant),
        json={"plan_code": "growth", "provider_customer_id": "cus_should_never_be_set"},
        headers=admin_headers,
    )
    assert response.status_code == 200
    assert response.json()["provider_customer_id"] is None


def test_platform_subscription_unknown_restaurant_returns_404(client, admin_headers):
    assert client.get(_platform_subscription_url(999999), headers=admin_headers).status_code == 404
    assert client.patch(_platform_subscription_url(999999), json={"plan_code": "growth"}, headers=admin_headers).status_code == 404


# --- Restaurant list additive fields ---

def test_restaurant_list_includes_plan_and_subscription_status(client, admin_headers, second_restaurant):
    response = client.get("/admin/platform/restaurants", headers=admin_headers)
    assert response.status_code == 200
    by_id = {r["id"]: r for r in response.json()}
    assert by_id[second_restaurant]["plan_code"] == "starter"
    assert by_id[second_restaurant]["subscription_status"] == "active"
    # Existing fields are still present and correct (additive, not replaced).
    assert by_id[second_restaurant]["seating_capacity"] > 0


# --- Usage counters ---

def test_usage_counts_conversations_created_this_calendar_month(client, admin_headers, second_restaurant, db):
    now = datetime.utcnow()
    this_month_start = datetime(now.year, now.month, 1)

    # Two conversations clearly within the current period.
    for i in range(2):
        db.add(models.Conversation(
            restaurant_id=second_restaurant,
            public_token=f"usage-test-token-{second_restaurant}-{i}",
            channel="web",
            created_at=this_month_start + timedelta(days=1, hours=i),
        ))
    # One conversation clearly in a PREVIOUS period, must not be counted.
    db.add(models.Conversation(
        restaurant_id=second_restaurant,
        public_token=f"usage-test-token-{second_restaurant}-old",
        channel="web",
        created_at=this_month_start - timedelta(days=5),
    ))
    db.commit()

    response = client.get(_subscription_url(second_restaurant), headers=admin_headers)
    assert response.status_code == 200
    assert response.json()["usage"]["conversations_this_period"] == 2


def test_usage_is_isolated_per_restaurant(client, admin_headers, second_restaurant, db):
    now = datetime.utcnow()
    db.add(models.Conversation(
        restaurant_id=1,
        public_token="usage-isolation-restaurant-1",
        channel="web",
        created_at=now,
    ))
    db.commit()

    response = client.get(_subscription_url(second_restaurant), headers=admin_headers)
    assert response.status_code == 200
    # Whatever restaurant 1's own count is, it must not leak into a
    # completely different, freshly-onboarded restaurant's usage.
    assert response.json()["usage"]["conversations_this_period"] == 0
