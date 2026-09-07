"""
POST/DELETE /admin/restaurant/{id}/widget-config/origins/... (Stage 4
Phase D — strict per-restaurant CORS: allowed-origin management).
Restaurant-scoped exactly like every other widget-config admin endpoint
(Stage 4 Phase A): reuses get_current_admin + require_restaurant_access,
no new authentication system. Actual CORS enforcement using these rows
is covered separately in tests/test_widget_cors.py — this file only
covers the CRUD surface and its authorization/validation rules.
"""

import pytest
from sqlalchemy.exc import IntegrityError

from app import models


def _widget_config_url(restaurant_id):
    return f"/admin/restaurant/{restaurant_id}/widget-config"


def _origins_url(restaurant_id):
    return f"/admin/restaurant/{restaurant_id}/widget-config/origins"


def _origin_url(restaurant_id, origin_id):
    return f"/admin/restaurant/{restaurant_id}/widget-config/origins/{origin_id}"


def _ensure_widget_config(client, admin_headers, restaurant_id):
    response = client.post(_widget_config_url(restaurant_id), json={}, headers=admin_headers)
    assert response.status_code == 201
    return response.json()


# --- Auth ---

def test_post_origin_requires_a_key_at_all(client):
    response = client.post(_origins_url(1), json={"origin": "https://example.com"})
    assert response.status_code == 401


def test_delete_origin_requires_a_key_at_all(client):
    response = client.delete(_origin_url(1, 1))
    assert response.status_code == 401


def test_scoped_admin_cannot_manage_origins_for_a_restaurant_it_is_not_granted(
    client, admin_headers, second_restaurant, scoped_admin_key
):
    _ensure_widget_config(client, admin_headers, second_restaurant)
    _, headers = scoped_admin_key([1])  # NOT granted second_restaurant

    post_response = client.post(
        _origins_url(second_restaurant),
        json={"origin": "https://example.com"},
        headers=headers,
    )
    assert post_response.status_code == 404

    delete_response = client.delete(_origin_url(second_restaurant, 1), headers=headers)
    assert delete_response.status_code == 404


def test_scoped_admin_can_manage_origins_for_its_own_granted_restaurant(
    client, admin_headers, second_restaurant, scoped_admin_key
):
    _ensure_widget_config(client, admin_headers, second_restaurant)
    _, headers = scoped_admin_key([second_restaurant])

    response = client.post(
        _origins_url(second_restaurant),
        json={"origin": "https://example.com"},
        headers=headers,
    )
    assert response.status_code == 201


def test_superadmin_can_manage_origins_for_any_restaurant(client, admin_headers, second_restaurant):
    _ensure_widget_config(client, admin_headers, second_restaurant)
    response = client.post(
        _origins_url(second_restaurant),
        json={"origin": "https://example.com"},
        headers=admin_headers,
    )
    assert response.status_code == 201


def test_unknown_restaurant_returns_404(client, admin_headers):
    response = client.post(
        _origins_url(999999), json={"origin": "https://example.com"}, headers=admin_headers
    )
    assert response.status_code == 404


# --- No widget config yet ---

def test_adding_an_origin_before_any_widget_config_exists_returns_404(
    client, admin_headers, second_restaurant
):
    response = client.post(
        _origins_url(second_restaurant),
        json={"origin": "https://example.com"},
        headers=admin_headers,
    )
    assert response.status_code == 404


# --- Create / list (via GET widget-config) ---

def test_create_allowed_origin(client, admin_headers, second_restaurant):
    _ensure_widget_config(client, admin_headers, second_restaurant)
    response = client.post(
        _origins_url(second_restaurant),
        json={"origin": "https://example.com"},
        headers=admin_headers,
    )
    assert response.status_code == 201
    body = response.json()
    assert body["origin"] == "https://example.com"
    assert isinstance(body["id"], int)
    assert "created_at" in body


def test_created_origin_appears_in_widget_config_get(client, admin_headers, second_restaurant):
    _ensure_widget_config(client, admin_headers, second_restaurant)
    client.post(
        _origins_url(second_restaurant),
        json={"origin": "https://example.com"},
        headers=admin_headers,
    )
    response = client.get(_widget_config_url(second_restaurant), headers=admin_headers)
    assert response.status_code == 200
    origins = response.json()["allowed_origins"]
    assert len(origins) == 1
    assert origins[0]["origin"] == "https://example.com"


def test_multiple_origins_can_be_registered(client, admin_headers, second_restaurant):
    _ensure_widget_config(client, admin_headers, second_restaurant)
    for origin in ("https://example.com", "https://www.example.com", "http://localhost:5500"):
        response = client.post(
            _origins_url(second_restaurant), json={"origin": origin}, headers=admin_headers
        )
        assert response.status_code == 201

    response = client.get(_widget_config_url(second_restaurant), headers=admin_headers)
    origins = {row["origin"] for row in response.json()["allowed_origins"]}
    assert origins == {"https://example.com", "https://www.example.com", "http://localhost:5500"}


def test_widget_config_with_no_origins_returns_an_empty_list(client, admin_headers, second_restaurant):
    _ensure_widget_config(client, admin_headers, second_restaurant)
    response = client.get(_widget_config_url(second_restaurant), headers=admin_headers)
    assert response.json()["allowed_origins"] == []


# --- Duplicate handling ---

def test_duplicate_origin_for_the_same_widget_is_rejected(client, admin_headers, second_restaurant):
    _ensure_widget_config(client, admin_headers, second_restaurant)
    first = client.post(
        _origins_url(second_restaurant), json={"origin": "https://example.com"}, headers=admin_headers
    )
    assert first.status_code == 201

    second = client.post(
        _origins_url(second_restaurant), json={"origin": "https://example.com"}, headers=admin_headers
    )
    assert second.status_code == 409


def test_the_same_literal_origin_can_be_registered_by_two_different_restaurants(
    client, admin_headers, second_restaurant
):
    _ensure_widget_config(client, admin_headers, 1)
    _ensure_widget_config(client, admin_headers, second_restaurant)

    response_1 = client.post(
        _origins_url(1), json={"origin": "http://127.0.0.1:5500"}, headers=admin_headers
    )
    response_2 = client.post(
        _origins_url(second_restaurant), json={"origin": "http://127.0.0.1:5500"}, headers=admin_headers
    )
    assert response_1.status_code == 201
    assert response_2.status_code == 201


def test_duplicate_origin_at_the_database_level_is_unique_per_widget_config(client, admin_headers, db):
    """
    Defense in depth, same shape as test_widget_config_admin.py's
    DB-level widget_key uniqueness test: even bypassing the application's
    own check-before-insert, the database itself must refuse a duplicate
    (widget_config_id, origin) pair.
    """
    config = _ensure_widget_config(client, admin_headers, 1)
    config_id = config["id"]

    db.add(models.WidgetAllowedOrigin(widget_config_id=config_id, origin="https://dup-test.example.com"))
    db.commit()

    db.add(models.WidgetAllowedOrigin(widget_config_id=config_id, origin="https://dup-test.example.com"))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


# --- Validation ---

def test_invalid_origin_formats_are_rejected(client, admin_headers, second_restaurant):
    _ensure_widget_config(client, admin_headers, second_restaurant)
    bad_origins = [
        "not-a-url",
        "ftp://example.com",
        "https://example.com/",
        "https://example.com/path",
        "https://example.com?query=1",
        "https://example.com#fragment",
        "//example.com",
        "example.com",
        "",
        "*",
        "https://*.example.com",
    ]
    for origin in bad_origins:
        response = client.post(
            _origins_url(second_restaurant), json={"origin": origin}, headers=admin_headers
        )
        assert response.status_code == 422, f"expected rejection for {origin!r}"


def test_valid_origin_formats_are_accepted(client, admin_headers, second_restaurant):
    _ensure_widget_config(client, admin_headers, second_restaurant)
    good_origins = [
        "https://example.com",
        "http://example.com",
        "https://example.com:8443",
        "http://localhost:5500",
        "http://127.0.0.1:3000",
    ]
    for origin in good_origins:
        response = client.post(
            _origins_url(second_restaurant), json={"origin": origin}, headers=admin_headers
        )
        assert response.status_code == 201, f"expected acceptance for {origin!r}"
        client.delete(
            _origin_url(second_restaurant, response.json()["id"]), headers=admin_headers
        )


def test_origin_is_normalized_to_lowercase_scheme_and_host(client, admin_headers, second_restaurant):
    _ensure_widget_config(client, admin_headers, second_restaurant)
    response = client.post(
        _origins_url(second_restaurant),
        json={"origin": "HTTPS://Example.COM"},
        headers=admin_headers,
    )
    assert response.status_code == 201
    assert response.json()["origin"] == "https://example.com"


# --- Delete ---

def test_delete_removes_the_origin(client, admin_headers, second_restaurant):
    _ensure_widget_config(client, admin_headers, second_restaurant)
    created = client.post(
        _origins_url(second_restaurant), json={"origin": "https://example.com"}, headers=admin_headers
    ).json()

    delete_response = client.delete(_origin_url(second_restaurant, created["id"]), headers=admin_headers)
    assert delete_response.status_code == 200

    get_response = client.get(_widget_config_url(second_restaurant), headers=admin_headers)
    assert get_response.json()["allowed_origins"] == []


def test_delete_unknown_origin_id_returns_404(client, admin_headers, second_restaurant):
    _ensure_widget_config(client, admin_headers, second_restaurant)
    response = client.delete(_origin_url(second_restaurant, 999999), headers=admin_headers)
    assert response.status_code == 404


def test_delete_cannot_remove_another_restaurants_origin(client, admin_headers, second_restaurant):
    """
    Cross-restaurant deletion prevention: an origin_id that genuinely
    exists — just under a DIFFERENT restaurant's widget config — must
    not be deletable through this restaurant's endpoint, even though the
    caller is a fully authorized superadmin.
    """
    _ensure_widget_config(client, admin_headers, 1)
    _ensure_widget_config(client, admin_headers, second_restaurant)

    origin_under_restaurant_1 = client.post(
        _origins_url(1), json={"origin": "https://restaurant-1-only.example.com"}, headers=admin_headers
    ).json()

    response = client.delete(
        _origin_url(second_restaurant, origin_under_restaurant_1["id"]), headers=admin_headers
    )
    assert response.status_code == 404

    # And it's still there, unaffected, under its real owner.
    get_response = client.get(_widget_config_url(1), headers=admin_headers)
    origins = {row["origin"] for row in get_response.json()["allowed_origins"]}
    assert "https://restaurant-1-only.example.com" in origins


def test_scoped_admin_cannot_delete_origins_for_a_restaurant_it_is_not_granted(
    client, admin_headers, second_restaurant, scoped_admin_key
):
    _ensure_widget_config(client, admin_headers, second_restaurant)
    created = client.post(
        _origins_url(second_restaurant), json={"origin": "https://example.com"}, headers=admin_headers
    ).json()

    _, headers = scoped_admin_key([1])  # NOT granted second_restaurant
    response = client.delete(_origin_url(second_restaurant, created["id"]), headers=headers)
    assert response.status_code == 404


# --- No leakage ---

def test_response_never_contains_admin_key_or_any_secret(client, admin_headers, second_restaurant):
    _ensure_widget_config(client, admin_headers, second_restaurant)
    response = client.post(
        _origins_url(second_restaurant), json={"origin": "https://example.com"}, headers=admin_headers
    )
    body_text = str(response.json())
    assert admin_headers["X-Admin-API-Key"] not in body_text
