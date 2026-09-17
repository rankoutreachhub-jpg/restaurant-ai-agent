"""
POST /admin/restaurant/{id}/checkout-session (Jantar SaaS Phase 4.1 —
authenticated Paddle checkout association; see
app/paddle_checkout_tokens.py's module docstring). Mirrors the existing
authorization test conventions in tests/test_subscription_visibility.py
for the sibling GET .../subscription endpoint — this endpoint uses the
exact same get_current_admin + require_restaurant_access dependencies,
so it must behave identically for "who may touch restaurant_id X".

Does NOT touch app/paddle_webhooks.py at all -- see
tests/test_paddle_webhooks.py for webhook-side resolution tests, and
tests/test_paddle_checkout_tokens.py for the pure token unit tests this
endpoint's issue_checkout_token() call relies on.
"""

from app import config, models
from app.paddle_checkout_tokens import verify_checkout_token


def _checkout_session_url(restaurant_id: int) -> str:
    return f"/admin/restaurant/{restaurant_id}/checkout-session"


def test_no_api_key_is_rejected(client):
    response = client.post(_checkout_session_url(1))
    assert response.status_code == 401


def test_scoped_admin_cannot_start_checkout_for_a_restaurant_it_is_not_granted(
    client, second_restaurant, scoped_admin_key
):
    _, headers = scoped_admin_key([1])  # NOT granted second_restaurant
    response = client.post(_checkout_session_url(second_restaurant), headers=headers)
    assert response.status_code == 404


def test_scoped_admin_can_start_checkout_for_its_own_granted_restaurant(
    client, second_restaurant, scoped_admin_key
):
    _, headers = scoped_admin_key([second_restaurant])
    response = client.post(_checkout_session_url(second_restaurant), headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert set(body.keys()) == {"checkout_token"}
    assert verify_checkout_token(body["checkout_token"]) == second_restaurant


def test_superadmin_can_start_checkout_for_any_restaurant(client, admin_headers, second_restaurant):
    response = client.post(_checkout_session_url(second_restaurant), headers=admin_headers)
    assert response.status_code == 200
    assert verify_checkout_token(response.json()["checkout_token"]) == second_restaurant


def test_unknown_restaurant_returns_404(client, admin_headers):
    response = client.post(_checkout_session_url(999999), headers=admin_headers)
    assert response.status_code == 404


def test_response_never_exposes_the_signing_secret(client, admin_headers, second_restaurant):
    response = client.post(_checkout_session_url(second_restaurant), headers=admin_headers)
    assert response.status_code == 200
    assert config.PADDLE_CHECKOUT_TOKEN_SECRET not in response.text


def test_each_call_issues_a_fresh_token_not_a_cached_one(client, admin_headers, second_restaurant):
    first = client.post(_checkout_session_url(second_restaurant), headers=admin_headers).json()
    second = client.post(_checkout_session_url(second_restaurant), headers=admin_headers).json()
    assert first["checkout_token"] != second["checkout_token"]
    assert verify_checkout_token(first["checkout_token"]) == second_restaurant
    assert verify_checkout_token(second["checkout_token"]) == second_restaurant


def test_checkout_session_does_not_create_or_modify_any_subscription_row(
    client, admin_headers, second_restaurant, db
):
    before = (
        db.query(models.Subscription)
        .filter(models.Subscription.restaurant_id == second_restaurant)
        .one()
    )
    before_plan, before_status, before_provider = before.plan_code, before.status, before.billing_provider

    response = client.post(_checkout_session_url(second_restaurant), headers=admin_headers)
    assert response.status_code == 200

    after = (
        db.query(models.Subscription)
        .filter(models.Subscription.restaurant_id == second_restaurant)
        .one()
    )
    assert (after.plan_code, after.status, after.billing_provider) == (before_plan, before_status, before_provider)

    count = (
        db.query(models.Subscription)
        .filter(models.Subscription.restaurant_id == second_restaurant)
        .count()
    )
    assert count == 1, "issuing a checkout session must never create a second Subscription row"
