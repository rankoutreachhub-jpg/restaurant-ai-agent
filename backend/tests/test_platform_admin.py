"""
Platform-admin endpoints (Stage 3 Step 3): restaurant onboarding and
admin-user (scoped admin key) management. Every route here must accept
ONLY the platform superadmin key — never a restaurant-scoped admin key,
no matter how many restaurants it's scoped to, since these endpoints
can create other admin users or grant new restaurant access.
"""


def test_platform_endpoints_require_a_key_at_all(client):
    response = client.post(
        "/admin/platform/restaurants",
        json={"name": "X", "address": "X", "phone": "X", "email": "x@example.com"},
    )
    assert response.status_code == 401


def test_scoped_admin_key_cannot_use_platform_endpoints(client, second_restaurant, scoped_admin_key):
    _, headers = scoped_admin_key([second_restaurant])

    response = client.post(
        "/admin/platform/restaurants",
        json={"name": "X", "address": "X", "phone": "X", "email": "x@example.com"},
        headers=headers,
    )
    assert response.status_code == 403

    response = client.post(
        "/admin/platform/admin-users",
        json={"label": "sneaky", "restaurant_ids": [second_restaurant]},
        headers=headers,
    )
    assert response.status_code == 403

    response = client.get("/admin/platform/admin-users", headers=headers)
    assert response.status_code == 403

    response = client.get("/admin/platform/restaurants", headers=headers)
    assert response.status_code == 403


def test_list_restaurants_requires_a_key_at_all(client):
    response = client.get("/admin/platform/restaurants")
    assert response.status_code == 401


def test_list_restaurants_with_superadmin_key_includes_all_restaurants(
    client, second_restaurant, admin_headers
):
    response = client.get("/admin/platform/restaurants", headers=admin_headers)
    assert response.status_code == 200
    ids = {r["id"] for r in response.json()}
    assert {1, second_restaurant} <= ids
    for restaurant in response.json():
        assert set(restaurant.keys()) == {
            "id", "name", "address", "phone", "email",
            "map_link", "parking_notes", "seating_capacity",
        }


def test_create_restaurant_succeeds_with_superadmin_key(client, admin_headers):
    response = client.post(
        "/admin/platform/restaurants",
        json={
            "name": "The Ferry Inn",
            "address": "2 River Lane",
            "phone": "01000 000000",
            "email": "hello@theferryinn.example.com",
            "seating_capacity": 25,
        },
        headers=admin_headers,
    )
    assert response.status_code == 201
    body = response.json()["restaurant"]
    assert body["name"] == "The Ferry Inn"
    assert body["seating_capacity"] == 25


def test_create_admin_user_with_unknown_restaurant_id_fails(client, admin_headers):
    response = client.post(
        "/admin/platform/admin-users",
        json={"label": "ghost", "restaurant_ids": [999999]},
        headers=admin_headers,
    )
    assert response.status_code == 404


def test_admin_user_key_returned_once_and_never_retrievable_again(
    client, second_restaurant, admin_headers
):
    created = client.post(
        "/admin/platform/admin-users",
        json={"label": "front of house", "restaurant_ids": [second_restaurant]},
        headers=admin_headers,
    )
    assert created.status_code == 201
    body = created.json()
    assert "api_key" in body
    plaintext_key = body["api_key"]
    admin_user_id = body["id"]

    # The issued key actually works against the granted restaurant.
    check = client.get(
        f"/admin/restaurant/{second_restaurant}", headers={"X-Admin-API-Key": plaintext_key}
    )
    assert check.status_code == 200

    # But it is never retrievable again via any GET.
    get_one = client.get(f"/admin/platform/admin-users/{admin_user_id}", headers=admin_headers)
    assert get_one.status_code == 200
    assert "api_key" not in get_one.json()
    assert plaintext_key not in str(get_one.json())

    list_all = client.get("/admin/platform/admin-users", headers=admin_headers)
    assert list_all.status_code == 200
    assert plaintext_key not in str(list_all.json())


def test_list_admin_users_never_includes_key_hash_field_name(client, second_restaurant, admin_headers):
    client.post(
        "/admin/platform/admin-users",
        json={"label": "x", "restaurant_ids": [second_restaurant]},
        headers=admin_headers,
    )
    response = client.get("/admin/platform/admin-users", headers=admin_headers)
    assert response.status_code == 200
    for entry in response.json():
        assert "key_hash" not in entry
        assert "api_key" not in entry


def test_rotate_key_invalidates_old_key_immediately(client, second_restaurant, admin_headers):
    created = client.post(
        "/admin/platform/admin-users",
        json={"label": "rotation test", "restaurant_ids": [second_restaurant]},
        headers=admin_headers,
    ).json()
    old_key = created["api_key"]
    admin_user_id = created["id"]

    assert client.get(
        f"/admin/restaurant/{second_restaurant}", headers={"X-Admin-API-Key": old_key}
    ).status_code == 200

    rotated = client.post(
        f"/admin/platform/admin-users/{admin_user_id}/rotate-key", headers=admin_headers
    )
    assert rotated.status_code == 200
    new_key = rotated.json()["api_key"]
    assert new_key != old_key

    # Old key stops working immediately.
    assert client.get(
        f"/admin/restaurant/{second_restaurant}", headers={"X-Admin-API-Key": old_key}
    ).status_code == 401
    # New key works.
    assert client.get(
        f"/admin/restaurant/{second_restaurant}", headers={"X-Admin-API-Key": new_key}
    ).status_code == 200


def test_deactivating_admin_user_blocks_access_immediately(client, second_restaurant, admin_headers):
    created = client.post(
        "/admin/platform/admin-users",
        json={"label": "deactivation test", "restaurant_ids": [second_restaurant]},
        headers=admin_headers,
    ).json()
    key = created["api_key"]
    admin_user_id = created["id"]

    assert client.get(
        f"/admin/restaurant/{second_restaurant}", headers={"X-Admin-API-Key": key}
    ).status_code == 200

    deactivated = client.patch(
        f"/admin/platform/admin-users/{admin_user_id}",
        json={"is_active": False},
        headers=admin_headers,
    )
    assert deactivated.status_code == 200
    assert deactivated.json()["is_active"] is False

    assert client.get(
        f"/admin/restaurant/{second_restaurant}", headers={"X-Admin-API-Key": key}
    ).status_code == 401

    # Reactivating restores access with the SAME key (no rotation happened).
    reactivated = client.patch(
        f"/admin/platform/admin-users/{admin_user_id}",
        json={"is_active": True},
        headers=admin_headers,
    )
    assert reactivated.status_code == 200
    assert client.get(
        f"/admin/restaurant/{second_restaurant}", headers={"X-Admin-API-Key": key}
    ).status_code == 200


def test_grant_and_revoke_restaurant_access(client, second_restaurant, admin_headers):
    created = client.post(
        "/admin/platform/admin-users",
        json={"label": "grant/revoke test", "restaurant_ids": [second_restaurant]},
        headers=admin_headers,
    ).json()
    key = created["api_key"]
    admin_user_id = created["id"]

    # Not scoped to restaurant 1 yet.
    assert client.get("/admin/restaurant/1", headers={"X-Admin-API-Key": key}).status_code == 404

    grant = client.post(
        f"/admin/platform/admin-users/{admin_user_id}/restaurants/1", headers=admin_headers
    )
    assert grant.status_code == 200
    assert set(grant.json()["restaurant_ids"]) == {1, second_restaurant}
    assert client.get("/admin/restaurant/1", headers={"X-Admin-API-Key": key}).status_code == 200

    revoke = client.delete(
        f"/admin/platform/admin-users/{admin_user_id}/restaurants/1", headers=admin_headers
    )
    assert revoke.status_code == 200
    assert revoke.json()["restaurant_ids"] == [second_restaurant]
    assert client.get("/admin/restaurant/1", headers={"X-Admin-API-Key": key}).status_code == 404

    # The other grant (second_restaurant) is untouched.
    assert client.get(
        f"/admin/restaurant/{second_restaurant}", headers={"X-Admin-API-Key": key}
    ).status_code == 200


def test_duplicate_restaurant_access_grant_is_unique_at_the_database_level(client, second_restaurant):
    """
    Defense in depth: even if application code ever forgot the
    check-before-insert in the grant endpoint, the database itself
    must refuse a duplicate (admin_user_id, restaurant_id) row.
    """
    import pytest
    from sqlalchemy.exc import IntegrityError

    from app import models
    from app.database import SessionLocal

    session = SessionLocal()
    try:
        admin_user = models.AdminUser(key_id="ra_test_dup", key_hash="x", label="dup test")
        session.add(admin_user)
        session.flush()

        session.add(models.AdminRestaurantAccess(
            admin_user_id=admin_user.id, restaurant_id=second_restaurant
        ))
        session.commit()

        session.add(models.AdminRestaurantAccess(
            admin_user_id=admin_user.id, restaurant_id=second_restaurant
        ))
        with pytest.raises(IntegrityError):
            session.commit()
    finally:
        session.rollback()
        session.close()
