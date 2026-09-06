"""
Multi-tenant authorization (Stage 3 Step 3): a restaurant-scoped admin
key must be able to fully manage the restaurant(s) it's scoped to, and
must get a 404 — indistinguishable from "doesn't exist" — on every
restaurant it is NOT scoped to, across every restaurant-scoped
admin/booking endpoint. The platform superadmin key must continue to
access every restaurant, exactly as before this stage existed.
"""

import pytest


def _menu_item_payload(name="Lemonade"):
    return {"category": "Drinks", "name": name, "price": 2.50}


def _booking_payload():
    from datetime import date, timedelta

    return {
        "customer_name": "Test Customer",
        "phone": "07000000000",
        "email": "test@example.com",
        "booking_date": (date.today() + timedelta(days=1)).isoformat(),
        "booking_time": "13:00",
        "party_size": 2,
    }


# =========================================================
# Scoped admin: full access to ITS OWN restaurant
# =========================================================

def test_scoped_admin_can_manage_its_own_restaurant(client, second_restaurant, scoped_admin_key):
    _, headers = scoped_admin_key([second_restaurant])

    assert client.get(f"/admin/restaurant/{second_restaurant}", headers=headers).status_code == 200
    assert client.patch(
        f"/admin/restaurant/{second_restaurant}", json={"phone": "0117 111 1111"}, headers=headers
    ).status_code == 200
    assert client.get(f"/admin/restaurant/{second_restaurant}/menu", headers=headers).status_code == 200
    assert client.get(f"/admin/restaurant/{second_restaurant}/opening-hours", headers=headers).status_code == 200

    created = client.post(
        f"/admin/restaurant/{second_restaurant}/menu", json=_menu_item_payload(), headers=headers
    )
    assert created.status_code == 200
    menu_item_id = created.json()["menu_item"]["id"]
    assert client.patch(
        f"/admin/restaurant/{second_restaurant}/menu/{menu_item_id}",
        json={"price": 3.00},
        headers=headers,
    ).status_code == 200
    assert client.delete(
        f"/admin/restaurant/{second_restaurant}/menu/{menu_item_id}", headers=headers
    ).status_code == 200

    assert client.patch(
        f"/admin/restaurant/{second_restaurant}/opening-hours/Monday",
        json={"close_time": "23:00"},
        headers=headers,
    ).status_code == 200

    created_booking = client.post(
        f"/admin/restaurant/{second_restaurant}/bookings", json=_booking_payload(), headers=headers
    )
    assert created_booking.status_code == 201
    booking_id = created_booking.json()["id"]
    assert client.get(
        f"/admin/restaurant/{second_restaurant}/bookings", headers=headers
    ).status_code == 200
    assert client.get(
        f"/admin/restaurant/{second_restaurant}/bookings/{booking_id}", headers=headers
    ).status_code == 200
    assert client.patch(
        f"/admin/restaurant/{second_restaurant}/bookings/{booking_id}",
        json={"status": "cancelled"},
        headers=headers,
    ).status_code == 200
    assert client.delete(
        f"/admin/restaurant/{second_restaurant}/bookings/{booking_id}", headers=headers
    ).status_code == 200


# =========================================================
# Scoped admin: 404 (not 403) on every OTHER restaurant's endpoints
# =========================================================

def _cross_tenant_get_paths(restaurant_id):
    return [
        f"/admin/restaurant/{restaurant_id}",
        f"/admin/restaurant/{restaurant_id}/menu",
        f"/admin/restaurant/{restaurant_id}/opening-hours",
        f"/admin/restaurant/{restaurant_id}/bookings",
    ]


@pytest.mark.parametrize("path_suffix", [
    "",
    "/menu",
    "/opening-hours",
    "/bookings",
])
def test_scoped_admin_for_restaurant_b_gets_404_on_restaurant_a_gets(
    client, second_restaurant, scoped_admin_key, path_suffix
):
    # Scoped ONLY to restaurant B (second_restaurant) — restaurant A is
    # the seeded restaurant, id=1.
    _, headers = scoped_admin_key([second_restaurant])
    response = client.get(f"/admin/restaurant/1{path_suffix}", headers=headers)
    assert response.status_code == 404
    assert response.json()["detail"] == "Restaurant not found"


def test_scoped_admin_for_restaurant_a_gets_404_on_restaurant_b_menu_create(
    client, second_restaurant, scoped_admin_key
):
    _, headers = scoped_admin_key([1])  # scoped only to restaurant A
    response = client.post(
        f"/admin/restaurant/{second_restaurant}/menu", json=_menu_item_payload(), headers=headers
    )
    assert response.status_code == 404


def test_scoped_admin_for_restaurant_a_gets_404_on_restaurant_b_opening_hours_update(
    client, second_restaurant, scoped_admin_key
):
    _, headers = scoped_admin_key([1])
    response = client.patch(
        f"/admin/restaurant/{second_restaurant}/opening-hours/Monday",
        json={"close_time": "23:00"},
        headers=headers,
    )
    assert response.status_code == 404


def test_scoped_admin_for_restaurant_a_gets_404_on_restaurant_b_restaurant_patch(
    client, second_restaurant, scoped_admin_key
):
    _, headers = scoped_admin_key([1])
    response = client.patch(
        f"/admin/restaurant/{second_restaurant}", json={"phone": "0000000000"}, headers=headers
    )
    assert response.status_code == 404


def test_scoped_admin_cannot_create_booking_for_other_restaurant(
    client, second_restaurant, scoped_admin_key
):
    _, headers = scoped_admin_key([1])
    response = client.post(
        f"/admin/restaurant/{second_restaurant}/bookings", json=_booking_payload(), headers=headers
    )
    assert response.status_code == 404


def test_scoped_admin_cannot_read_update_or_delete_other_restaurant_booking(
    client, second_restaurant, scoped_admin_key, admin_headers
):
    # Create a real booking under restaurant B using the superadmin key...
    created = client.post(
        f"/admin/restaurant/{second_restaurant}/bookings", json=_booking_payload(), headers=admin_headers
    )
    assert created.status_code == 201
    booking_id = created.json()["id"]

    # ...then confirm an admin scoped only to restaurant A can't touch it.
    _, headers = scoped_admin_key([1])
    assert client.get(
        f"/admin/restaurant/{second_restaurant}/bookings/{booking_id}", headers=headers
    ).status_code == 404
    assert client.patch(
        f"/admin/restaurant/{second_restaurant}/bookings/{booking_id}",
        json={"status": "cancelled"},
        headers=headers,
    ).status_code == 404
    assert client.delete(
        f"/admin/restaurant/{second_restaurant}/bookings/{booking_id}", headers=headers
    ).status_code == 404

    # And prove it's genuinely untouched, not silently cancelled.
    still_there = client.get(
        f"/admin/restaurant/{second_restaurant}/bookings/{booking_id}", headers=admin_headers
    )
    assert still_there.status_code == 200
    assert still_there.json()["status"] == "confirmed"


# =========================================================
# Multi-restaurant scoped admin: access to BOTH granted restaurants
# =========================================================

def test_scoped_admin_with_both_restaurants_can_access_both(client, second_restaurant, scoped_admin_key):
    _, headers = scoped_admin_key([1, second_restaurant])
    assert client.get("/admin/restaurant/1", headers=headers).status_code == 200
    assert client.get(f"/admin/restaurant/{second_restaurant}", headers=headers).status_code == 200


# =========================================================
# Superadmin: continues to access every restaurant (regression)
# =========================================================

def test_superadmin_still_accesses_both_restaurants(client, second_restaurant, admin_headers):
    assert client.get("/admin/restaurant/1", headers=admin_headers).status_code == 200
    assert client.get(f"/admin/restaurant/{second_restaurant}", headers=admin_headers).status_code == 200
    assert client.get(f"/admin/restaurant/{second_restaurant}/bookings", headers=admin_headers).status_code == 200


# =========================================================
# An inactive/unknown scoped key behaves exactly like an invalid key
# =========================================================

def test_unknown_scoped_key_is_unauthorized(client):
    response = client.get(
        "/admin/restaurant/1", headers={"X-Admin-API-Key": "ra_deadbeef.not-a-real-secret"}
    )
    assert response.status_code == 401


def test_malformed_key_without_dot_is_unauthorized(client):
    response = client.get(
        "/admin/restaurant/1", headers={"X-Admin-API-Key": "not-shaped-like-a-scoped-key"}
    )
    assert response.status_code == 401
