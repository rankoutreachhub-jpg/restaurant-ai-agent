"""
Every /admin/* endpoint must reject requests with no admin key or the
wrong key (401), and accept requests with the correct key. This is what
protects restaurant, menu, and opening-hours management (and any
booking-management endpoints added later under the same router) from
unauthenticated access.
"""

import pytest

ADMIN_GET_ENDPOINTS = [
    "/admin/restaurant/1",
    "/admin/restaurant/1/menu",
    "/admin/restaurant/1/opening-hours",
]


@pytest.mark.parametrize("path", ADMIN_GET_ENDPOINTS)
def test_admin_get_without_key_is_unauthorized(client, path):
    response = client.get(path)
    assert response.status_code == 401
    assert "detail" in response.json()


@pytest.mark.parametrize("path", ADMIN_GET_ENDPOINTS)
def test_admin_get_with_wrong_key_is_unauthorized(client, path):
    response = client.get(path, headers={"X-Admin-API-Key": "totally-wrong-key"})
    assert response.status_code == 401


@pytest.mark.parametrize("path", ADMIN_GET_ENDPOINTS)
def test_admin_get_with_correct_key_succeeds(client, admin_headers, path):
    response = client.get(path, headers=admin_headers)
    assert response.status_code == 200


def test_admin_patch_restaurant_requires_key(client):
    response = client.patch("/admin/restaurant/1", json={"phone": "0000000000"})
    assert response.status_code == 401


def test_admin_patch_restaurant_with_key_succeeds(client, admin_headers):
    response = client.patch(
        "/admin/restaurant/1",
        json={"phone": "01962 999999"},
        headers=admin_headers,
    )
    assert response.status_code == 200
    assert response.json()["restaurant"]["phone"] == "01962 999999"


def test_admin_create_menu_item_requires_key(client):
    response = client.post(
        "/admin/restaurant/1/menu",
        json={"category": "Drinks", "name": "Lemonade", "price": 2.50},
    )
    assert response.status_code == 401


def test_admin_create_menu_item_with_key_succeeds(client, admin_headers):
    response = client.post(
        "/admin/restaurant/1/menu",
        json={"category": "Drinks", "name": "Lemonade", "price": 2.50},
        headers=admin_headers,
    )
    assert response.status_code == 200
    assert response.json()["menu_item"]["name"] == "Lemonade"


def test_admin_delete_menu_item_requires_key(client, admin_headers):
    # Create one first (with a valid key) so there's something to try to delete.
    created = client.post(
        "/admin/restaurant/1/menu",
        json={"category": "Drinks", "name": "Cola", "price": 2.00},
        headers=admin_headers,
    ).json()["menu_item"]

    response = client.delete(f"/admin/restaurant/1/menu/{created['id']}")
    assert response.status_code == 401


def test_admin_update_opening_hours_requires_key(client):
    response = client.patch(
        "/admin/restaurant/1/opening-hours/Monday",
        json={"close_time": "23:00"},
    )
    assert response.status_code == 401


def test_admin_update_opening_hours_with_key_succeeds(client, admin_headers):
    response = client.patch(
        "/admin/restaurant/1/opening-hours/Monday",
        json={"close_time": "23:00"},
        headers=admin_headers,
    )
    assert response.status_code == 200
    assert response.json()["opening_hours"]["close_time"] == "23:00"
