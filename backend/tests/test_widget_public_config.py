"""
GET /widget/{widget_key}/config (Stage 4 Phase B): the first genuinely
public, unauthenticated endpoint beyond /chat. Every test here calls it
with NO headers at all, proving that's sufficient — nothing about it is
secretly still gated by admin auth or rate limiting.
"""

WIDGET_PUBLIC_FIELDS = {
    "widget_key", "restaurant_name", "welcome_message",
    "primary_language", "logo_url", "accent_color", "booking_enabled",
}


def _config_url(widget_key):
    return f"/widget/{widget_key}/config"


def _create_widget_config(client, admin_headers, restaurant_id, **fields):
    response = client.post(
        f"/admin/restaurant/{restaurant_id}/widget-config",
        json=fields,
        headers=admin_headers,
    )
    assert response.status_code == 201
    return response.json()


# --- Valid key ---

def test_valid_widget_key_returns_public_config_with_no_auth_headers(client, admin_headers):
    created = _create_widget_config(
        client, admin_headers, 1,
        welcome_message="Hiya! Ask me about our menu.",
        primary_language="en-GB",
        logo_url="https://example.com/logo.png",
        accent_color="#7a2e2e",
        booking_enabled=True,
    )

    response = client.get(_config_url(created["widget_key"]))
    assert response.status_code == 200
    body = response.json()
    assert body["widget_key"] == created["widget_key"]
    assert body["restaurant_name"] == "The Kings Arms"
    assert body["welcome_message"] == "Hiya! Ask me about our menu."
    assert body["primary_language"] == "en-GB"
    assert body["logo_url"] == "https://example.com/logo.png"
    assert body["accent_color"] == "#7a2e2e"
    assert body["booking_enabled"] is True


def test_booking_enabled_false_is_reflected(client, admin_headers, second_restaurant):
    created = _create_widget_config(client, admin_headers, second_restaurant, booking_enabled=False)
    response = client.get(_config_url(created["widget_key"]))
    assert response.status_code == 200
    assert response.json()["booking_enabled"] is False


def test_welcome_message_falls_back_to_a_generated_default_containing_the_restaurant_name(
    client, admin_headers, second_restaurant
):
    created = _create_widget_config(client, admin_headers, second_restaurant)  # no welcome_message
    response = client.get(_config_url(created["widget_key"]))
    assert response.status_code == 200
    body = response.json()
    assert body["welcome_message"] is not None
    assert "The Anchor" in body["welcome_message"]


# --- Unknown / inactive key: identical generic 404 ---

def test_unknown_widget_key_returns_404(client):
    response = client.get(_config_url("wgt_this_key_was_never_issued"))
    assert response.status_code == 404


def test_inactive_widget_returns_404(client, admin_headers, second_restaurant):
    created = _create_widget_config(client, admin_headers, second_restaurant, is_active=False)
    response = client.get(_config_url(created["widget_key"]))
    assert response.status_code == 404


def test_unknown_and_inactive_keys_return_byte_for_byte_identical_responses(
    client, admin_headers, second_restaurant
):
    inactive = _create_widget_config(client, admin_headers, second_restaurant, is_active=False)

    unknown_response = client.get(_config_url("wgt_definitely_never_issued_00000"))
    inactive_response = client.get(_config_url(inactive["widget_key"]))

    assert unknown_response.status_code == inactive_response.status_code == 404
    assert unknown_response.json() == inactive_response.json()
    assert unknown_response.headers.get("content-length") == inactive_response.headers.get("content-length")


def test_reactivating_a_widget_makes_it_reachable_again(client, admin_headers, second_restaurant):
    created = _create_widget_config(client, admin_headers, second_restaurant, is_active=False)
    assert client.get(_config_url(created["widget_key"])).status_code == 404

    client.post(
        f"/admin/restaurant/{second_restaurant}/widget-config",
        json={"is_active": True},
        headers=admin_headers,
    )
    assert client.get(_config_url(created["widget_key"])).status_code == 200


# --- Malformed / garbage / very long keys ---

def test_malformed_or_garbage_keys_return_404_never_500(client):
    garbage_keys = [
        "not-a-real-key",
        "1",
        "' OR 1=1--",
        "<script>alert(1)</script>",
        "wgt_",
        "%00%00%00",
        "x" * 5000,
    ]
    for key in garbage_keys:
        response = client.get(_config_url(key))
        assert response.status_code == 404, f"expected 404 for {key!r}, got {response.status_code}"


def test_empty_widget_key_segment_is_not_found(client):
    # A trailing slash with nothing after it doesn't match this route at
    # all (FastAPI 404s on no route match, same externally-visible
    # outcome as a resolved-but-unknown key).
    response = client.get("/widget//config")
    assert response.status_code == 404


# --- Exact public field allowlist / no leakage ---

def test_response_contains_exactly_the_seven_public_fields(client, admin_headers, second_restaurant):
    created = _create_widget_config(client, admin_headers, second_restaurant, welcome_message="hi")
    response = client.get(_config_url(created["widget_key"]))
    assert set(response.json().keys()) == WIDGET_PUBLIC_FIELDS


def test_private_and_internal_fields_are_never_present(client, admin_headers, second_restaurant):
    created = _create_widget_config(client, admin_headers, second_restaurant, welcome_message="hi")
    response = client.get(_config_url(created["widget_key"]))
    body = response.json()

    for forbidden_field in (
        "restaurant_id", "id", "is_active", "created_at",
        "address", "phone", "email", "seating_capacity",
        "menu", "faqs", "api_key", "key_hash",
    ):
        assert forbidden_field not in body


def test_admin_key_never_appears_in_the_public_response(client, admin_headers, second_restaurant):
    created = _create_widget_config(client, admin_headers, second_restaurant, welcome_message="hi")
    response = client.get(_config_url(created["widget_key"]))
    assert admin_headers["X-Admin-API-Key"] not in str(response.json())


# --- Cross-tenant isolation ---

def test_two_restaurants_widgets_never_leak_each_others_data(client, admin_headers, second_restaurant):
    config_1 = _create_widget_config(
        client, admin_headers, 1,
        welcome_message="Restaurant 1's own welcome message",
        accent_color="#111111",
    )
    config_2 = _create_widget_config(
        client, admin_headers, second_restaurant,
        welcome_message="Restaurant 2's own welcome message",
        accent_color="#222222",
    )

    response_1 = client.get(_config_url(config_1["widget_key"])).json()
    response_2 = client.get(_config_url(config_2["widget_key"])).json()

    assert response_1["restaurant_name"] != response_2["restaurant_name"]
    assert response_1["welcome_message"] == "Restaurant 1's own welcome message"
    assert response_2["welcome_message"] == "Restaurant 2's own welcome message"
    assert response_1["accent_color"] == "#111111"
    assert response_2["accent_color"] == "#222222"

    # Requesting restaurant 1's key can never surface restaurant 2's data.
    assert "Restaurant 2" not in str(response_1)
    assert response_1["widget_key"] != response_2["widget_key"]
