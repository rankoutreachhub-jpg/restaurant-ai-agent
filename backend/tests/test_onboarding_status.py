"""
Superadmin restaurant onboarding readiness checklist.

GET /admin/platform/restaurants/onboarding-status (all restaurants) and
GET /admin/platform/restaurants/{id}/onboarding-status (one restaurant)
-- see app/onboarding_status.py for exactly which checks are computed
and which are blocking (profile, menu, opening hours, widget
configuration) vs. purely informational (FAQs, WhatsApp, subscription).
Both endpoints live on the existing /admin/platform router, which
already requires the platform superadmin key for every route on it
(see routers/platform_admin.py) -- no new authentication path.
"""

from app import models

ONBOARDING_URL = "/admin/platform/restaurants/onboarding-status"


def _restaurant_url(restaurant_id):
    return f"/admin/platform/restaurants/{restaurant_id}/onboarding-status"


def _checks_by_key(body):
    return {c["key"]: c for c in body["checks"]}


def _add_menu_item(client, admin_headers, restaurant_id):
    response = client.post(
        f"/admin/restaurant/{restaurant_id}/menu",
        json={"category": "Mains", "name": "Test Dish", "price": 9.99},
        headers=admin_headers,
    )
    assert response.status_code == 200, response.text


def _add_faq(client, admin_headers, restaurant_id):
    response = client.post(
        f"/admin/restaurant/{restaurant_id}/faqs",
        json={"question": "Do you take walk-ins?", "answer": "Yes, when we have space."},
        headers=admin_headers,
    )
    assert response.status_code == 201, response.text


def _activate_widget(client, admin_headers, restaurant_id):
    response = client.post(
        f"/admin/restaurant/{restaurant_id}/widget-config",
        json={"is_active": True},
        headers=admin_headers,
    )
    assert response.status_code == 201, response.text


def _add_widget_origin(client, admin_headers, restaurant_id, origin="https://example.com"):
    response = client.post(
        f"/admin/restaurant/{restaurant_id}/widget-config/origins",
        json={"origin": origin},
        headers=admin_headers,
    )
    assert response.status_code == 201, response.text


def _set_plan(client, admin_headers, restaurant_id, plan_code=None, status=None):
    data = {}
    if plan_code is not None:
        data["plan_code"] = plan_code
    if status is not None:
        data["status"] = status
    response = client.patch(
        f"/admin/platform/restaurants/{restaurant_id}/subscription",
        json=data,
        headers=admin_headers,
    )
    assert response.status_code == 200, response.text


def _complete_profile(client, admin_headers, restaurant_id):
    """
    second_restaurant (tests/conftest.py) is onboarded without a
    map_link -- deliberately, so test_profile_missing_map_link_below has
    something real to exercise. Every other test in this file that isn't
    specifically about the profile check needs a complete profile first,
    so its overall_status reflects the OTHER check being exercised.
    """
    response = client.patch(
        f"/admin/restaurant/{restaurant_id}",
        json={"map_link": "https://maps.example.com/the-anchor"},
        headers=admin_headers,
    )
    assert response.status_code == 200, response.text


def _onboard_restaurant(client, admin_headers, **overrides):
    data = {
        "name": "Onboarding Status Test Restaurant",
        "address": "1 Test Lane",
        "phone": "01000 000000",
        "email": "onboarding-status-test@example.com",
        "map_link": "https://maps.example.com/test",
        "seating_capacity": 20,
    }
    data.update(overrides)
    response = client.post("/admin/platform/restaurants", json=data, headers=admin_headers)
    assert response.status_code == 201, response.text
    return response.json()["restaurant"]["id"]


# --- Authorization / tenant isolation ---

def test_requires_a_key_at_all(client):
    assert client.get(ONBOARDING_URL).status_code == 401
    assert client.get(_restaurant_url(1)).status_code == 401


def test_scoped_admin_key_cannot_use_either_endpoint(client, second_restaurant, scoped_admin_key):
    _, headers = scoped_admin_key([second_restaurant])
    assert client.get(ONBOARDING_URL, headers=headers).status_code == 403
    assert client.get(_restaurant_url(second_restaurant), headers=headers).status_code == 403


def test_unknown_restaurant_returns_404(client, admin_headers):
    assert client.get(_restaurant_url(999999), headers=admin_headers).status_code == 404


def test_single_restaurant_endpoint_only_returns_that_restaurants_own_data(
    client, admin_headers, second_restaurant
):
    """
    Tenant isolation: asking for restaurant 1's status must never return
    (or be influenced by) second_restaurant's data, and vice versa --
    each restaurant's checklist reflects only its own rows.
    """
    _add_menu_item(client, admin_headers, second_restaurant)  # restaurant 1 has no menu items added here

    body_1 = client.get(_restaurant_url(1), headers=admin_headers).json()
    body_2 = client.get(_restaurant_url(second_restaurant), headers=admin_headers).json()

    assert body_1["restaurant_id"] == 1
    assert body_2["restaurant_id"] == second_restaurant
    # The seeded restaurant 1 already has its own menu items (seed_data.py);
    # what matters is that second_restaurant's freshly-added item didn't
    # leak into restaurant 1's count and each restaurant reports its own.
    assert _checks_by_key(body_2)["menu"]["detail"] == "1 menu item(s)."


def test_bulk_endpoint_includes_every_restaurant(client, admin_headers, second_restaurant):
    response = client.get(ONBOARDING_URL, headers=admin_headers)
    assert response.status_code == 200
    ids = {row["restaurant_id"] for row in response.json()}
    assert {1, second_restaurant} <= ids


# --- Individual checks ---

def test_fully_configured_restaurant_is_ready(client, admin_headers, second_restaurant):
    """second_restaurant already has opening hours (conftest fixture) and
    an auto-provisioned (inactive) widget config -- filling in the rest
    should make every blocking check complete."""
    _complete_profile(client, admin_headers, second_restaurant)
    _add_menu_item(client, admin_headers, second_restaurant)
    _add_faq(client, admin_headers, second_restaurant)
    _activate_widget(client, admin_headers, second_restaurant)
    _add_widget_origin(client, admin_headers, second_restaurant)
    _set_plan(client, admin_headers, second_restaurant, plan_code="growth")

    from app.database import SessionLocal
    db = SessionLocal()
    try:
        db.add(models.WhatsAppNumber(
            restaurant_id=second_restaurant,
            phone_number_id="1234567890",
            display_phone_number="+15551234567",
        ))
        db.commit()
    finally:
        db.close()

    body = client.get(_restaurant_url(second_restaurant), headers=admin_headers).json()
    checks = _checks_by_key(body)

    assert checks["profile"]["status"] == "complete"
    assert checks["menu"]["status"] == "complete"
    assert checks["opening_hours"]["status"] == "complete"
    assert checks["faqs"]["status"] == "complete"
    assert checks["widget"]["status"] == "complete"
    assert checks["whatsapp"]["status"] == "complete"
    assert checks["subscription"]["status"] == "complete"
    assert body["overall_status"] == "ready"


def test_missing_menu_is_incomplete_and_blocking(client, admin_headers, second_restaurant):
    _complete_profile(client, admin_headers, second_restaurant)
    body = client.get(_restaurant_url(second_restaurant), headers=admin_headers).json()
    menu_check = _checks_by_key(body)["menu"]

    assert menu_check["status"] == "needs_setup"
    assert menu_check["blocking"] is True
    assert menu_check["next_actions"] == ["Add menu items"]
    assert body["overall_status"] == "incomplete"


def test_missing_opening_hours_is_incomplete_and_blocking(client, admin_headers):
    # Deliberately NOT using second_restaurant (whose fixture already adds
    # a full week of hours) -- onboard a plain restaurant with none at all.
    restaurant_id = _onboard_restaurant(client, admin_headers, email="no-hours@example.com")

    body = client.get(_restaurant_url(restaurant_id), headers=admin_headers).json()
    hours_check = _checks_by_key(body)["opening_hours"]

    assert hours_check["status"] == "needs_setup"
    assert hours_check["blocking"] is True
    assert hours_check["detail"] == "0 of 7 days configured with usable hours."
    assert hours_check["next_actions"] == ["Configure opening hours"]
    assert body["overall_status"] == "incomplete"


def test_missing_faq_is_labeled_optional_and_never_blocks(client, admin_headers, second_restaurant):
    _complete_profile(client, admin_headers, second_restaurant)
    _add_menu_item(client, admin_headers, second_restaurant)
    _activate_widget(client, admin_headers, second_restaurant)
    _add_widget_origin(client, admin_headers, second_restaurant)

    body = client.get(_restaurant_url(second_restaurant), headers=admin_headers).json()
    faq_check = _checks_by_key(body)["faqs"]

    assert faq_check["status"] == "optional_not_configured"
    assert faq_check["blocking"] is False
    # Every OTHER blocking check is satisfied -- the missing (optional)
    # FAQ must not be the thing standing between this restaurant and "ready".
    assert body["overall_status"] == "ready"


def test_inactive_widget_is_incomplete_and_blocking(client, admin_headers, second_restaurant):
    _complete_profile(client, admin_headers, second_restaurant)
    _add_menu_item(client, admin_headers, second_restaurant)
    _add_widget_origin(client, admin_headers, second_restaurant)
    # Deliberately never activated -- stays at its auto-provisioned default.

    body = client.get(_restaurant_url(second_restaurant), headers=admin_headers).json()
    widget_check = _checks_by_key(body)["widget"]

    assert widget_check["status"] == "needs_setup"
    assert widget_check["blocking"] is True
    assert "Inactive" in widget_check["detail"]
    assert "Activate the widget when ready" in widget_check["next_actions"]
    assert body["overall_status"] == "incomplete"


def test_missing_allowed_origin_shows_a_clear_warning(client, admin_headers, second_restaurant):
    _activate_widget(client, admin_headers, second_restaurant)
    # Deliberately no origins added.

    body = client.get(_restaurant_url(second_restaurant), headers=admin_headers).json()
    widget_check = _checks_by_key(body)["widget"]

    assert widget_check["status"] == "needs_setup"
    assert widget_check["blocking"] is True
    assert "Website origin not configured" in widget_check["detail"]
    assert "Add website origin" in widget_check["next_actions"]


def test_whatsapp_optional_on_a_plan_that_doesnt_include_it(client, admin_headers, second_restaurant):
    # Default plan (starter) does not include WhatsApp.
    body = client.get(_restaurant_url(second_restaurant), headers=admin_headers).json()
    whatsapp_check = _checks_by_key(body)["whatsapp"]

    assert whatsapp_check["status"] == "optional_not_configured"
    assert whatsapp_check["blocking"] is False
    assert whatsapp_check["next_actions"] == []


def test_whatsapp_needs_setup_but_still_non_blocking_on_a_plan_that_includes_it(
    client, admin_headers, second_restaurant
):
    _complete_profile(client, admin_headers, second_restaurant)
    _set_plan(client, admin_headers, second_restaurant, plan_code="growth")
    _add_menu_item(client, admin_headers, second_restaurant)
    _add_faq(client, admin_headers, second_restaurant)
    _activate_widget(client, admin_headers, second_restaurant)
    _add_widget_origin(client, admin_headers, second_restaurant)

    body = client.get(_restaurant_url(second_restaurant), headers=admin_headers).json()
    checks = _checks_by_key(body)

    assert checks["whatsapp"]["status"] == "needs_setup"
    assert checks["whatsapp"]["blocking"] is False
    assert checks["whatsapp"]["next_actions"] == ["Connect WhatsApp"]
    # Every blocking check is satisfied -- WhatsApp being unconnected must
    # not, by itself, keep this restaurant out of "ready".
    assert body["overall_status"] == "ready"


def test_whatsapp_connected_is_reported_complete(client, admin_headers, second_restaurant, whatsapp_number):
    # An explicit, test-specific phone_number_id -- WhatsAppNumber.phone_number_id
    # is globally unique and the test DB persists for the whole session, so
    # relying on the whatsapp_number fixture's shared default here would risk
    # colliding with another test file's own use of that same default.
    whatsapp_number(second_restaurant, phone_number_id="9990001112223")
    body = client.get(_restaurant_url(second_restaurant), headers=admin_headers).json()
    whatsapp_check = _checks_by_key(body)["whatsapp"]

    assert whatsapp_check["status"] == "complete"
    assert whatsapp_check["blocking"] is False


def test_subscription_status_is_shown_and_never_blocking(client, admin_headers, second_restaurant):
    body = client.get(_restaurant_url(second_restaurant), headers=admin_headers).json()
    subscription_check = _checks_by_key(body)["subscription"]
    assert subscription_check["blocking"] is False
    assert subscription_check["status"] == "complete"
    assert "Starter" in subscription_check["detail"]

    _set_plan(client, admin_headers, second_restaurant, status="past_due")
    body = client.get(_restaurant_url(second_restaurant), headers=admin_headers).json()
    subscription_check = _checks_by_key(body)["subscription"]
    # Flagged for visibility, but still never blocking -- no enforcement.
    assert subscription_check["status"] == "needs_setup"
    assert subscription_check["blocking"] is False


def test_profile_missing_map_link_is_critical_setup_missing(client, admin_headers):
    restaurant_id = _onboard_restaurant(
        client, admin_headers, email="no-map-link@example.com", map_link=None
    )

    body = client.get(_restaurant_url(restaurant_id), headers=admin_headers).json()
    profile_check = _checks_by_key(body)["profile"]

    assert profile_check["status"] == "needs_setup"
    assert profile_check["blocking"] is True
    assert "map link" in profile_check["detail"]
    assert body["overall_status"] == "critical_setup_missing"
