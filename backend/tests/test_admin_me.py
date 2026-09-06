"""
GET /admin/me (Stage 3 Step 6A): lets an admin-facing client (the
dashboard) discover its own scope without guessing — never anyone
else's. Same auth as every other admin route (get_current_admin);
not restaurant-scoped, so require_restaurant_access doesn't apply here.
"""


def test_me_requires_admin_key(client):
    response = client.get("/admin/me")
    assert response.status_code == 401


def test_me_for_superadmin(client, admin_headers):
    response = client.get("/admin/me", headers=admin_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["is_superadmin"] is True
    assert body["restaurant_ids"] is None


def test_me_for_scoped_admin_reports_exactly_its_own_restaurants(
    client, second_restaurant, scoped_admin_key
):
    _, headers = scoped_admin_key([1, second_restaurant])
    response = client.get("/admin/me", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["is_superadmin"] is False
    assert sorted(body["restaurant_ids"]) == sorted([1, second_restaurant])


def test_me_for_scoped_admin_never_reports_unscoped_restaurants(
    client, second_restaurant, scoped_admin_key
):
    _, headers = scoped_admin_key([1])  # NOT granted second_restaurant
    response = client.get("/admin/me", headers=headers)
    assert response.status_code == 200
    assert response.json()["restaurant_ids"] == [1]
