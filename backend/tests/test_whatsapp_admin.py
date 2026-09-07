"""
POST/DELETE /admin/platform/restaurants/{id}/whatsapp-number (Stage 3
Step 6B): superadmin-only, consistent with every other endpoint in
app/routers/platform_admin.py (router-level require_superadmin — see
that file's dependencies=[...]).
"""


def test_set_whatsapp_number_requires_a_key_at_all(client):
    response = client.post(
        "/admin/platform/restaurants/1/whatsapp-number",
        json={"phone_number_id": "111", "display_phone_number": "+15550001111"},
    )
    assert response.status_code == 401


def test_scoped_admin_key_cannot_set_whatsapp_number(client, scoped_admin_key):
    _, headers = scoped_admin_key([1])
    response = client.post(
        "/admin/platform/restaurants/1/whatsapp-number",
        json={"phone_number_id": "111", "display_phone_number": "+15550001111"},
        headers=headers,
    )
    assert response.status_code == 403


def test_scoped_admin_key_cannot_delete_whatsapp_number(client, scoped_admin_key):
    _, headers = scoped_admin_key([1])
    response = client.delete("/admin/platform/restaurants/1/whatsapp-number", headers=headers)
    assert response.status_code == 403


def test_superadmin_can_set_whatsapp_number(client, admin_headers):
    response = client.post(
        "/admin/platform/restaurants/1/whatsapp-number",
        json={"phone_number_id": "6000000000000001", "display_phone_number": "+15550001111"},
        headers=admin_headers,
    )
    assert response.status_code == 201
    body = response.json()
    assert body["restaurant_id"] == 1
    assert body["phone_number_id"] == "6000000000000001"


def test_set_whatsapp_number_for_unknown_restaurant_returns_404(client, admin_headers):
    response = client.post(
        "/admin/platform/restaurants/999999/whatsapp-number",
        json={"phone_number_id": "6000000000000002", "display_phone_number": "+15550001111"},
        headers=admin_headers,
    )
    assert response.status_code == 404


def test_re_posting_replaces_the_existing_mapping(client, admin_headers):
    client.post(
        "/admin/platform/restaurants/1/whatsapp-number",
        json={"phone_number_id": "6000000000000003", "display_phone_number": "+15550001111"},
        headers=admin_headers,
    )
    second = client.post(
        "/admin/platform/restaurants/1/whatsapp-number",
        json={"phone_number_id": "6000000000000004", "display_phone_number": "+15550002222"},
        headers=admin_headers,
    )
    assert second.status_code == 201
    assert second.json()["phone_number_id"] == "6000000000000004"


def test_phone_number_id_already_mapped_to_a_different_restaurant_is_rejected(
    client, admin_headers, second_restaurant
):
    client.post(
        "/admin/platform/restaurants/1/whatsapp-number",
        json={"phone_number_id": "6000000000000005", "display_phone_number": "+15550001111"},
        headers=admin_headers,
    )
    conflict = client.post(
        f"/admin/platform/restaurants/{second_restaurant}/whatsapp-number",
        json={"phone_number_id": "6000000000000005", "display_phone_number": "+15550003333"},
        headers=admin_headers,
    )
    assert conflict.status_code == 409


def test_superadmin_can_delete_whatsapp_number(client, admin_headers):
    client.post(
        "/admin/platform/restaurants/1/whatsapp-number",
        json={"phone_number_id": "6000000000000006", "display_phone_number": "+15550001111"},
        headers=admin_headers,
    )
    response = client.delete("/admin/platform/restaurants/1/whatsapp-number", headers=admin_headers)
    assert response.status_code == 200


def test_deleting_when_none_mapped_is_not_an_error(client, admin_headers, second_restaurant):
    response = client.delete(
        f"/admin/platform/restaurants/{second_restaurant}/whatsapp-number", headers=admin_headers
    )
    assert response.status_code == 200


def test_deleted_mapping_can_be_reassigned_to_a_different_restaurant(
    client, admin_headers, second_restaurant
):
    client.post(
        "/admin/platform/restaurants/1/whatsapp-number",
        json={"phone_number_id": "6000000000000007", "display_phone_number": "+15550001111"},
        headers=admin_headers,
    )
    client.delete("/admin/platform/restaurants/1/whatsapp-number", headers=admin_headers)

    reassigned = client.post(
        f"/admin/platform/restaurants/{second_restaurant}/whatsapp-number",
        json={"phone_number_id": "6000000000000007", "display_phone_number": "+15550004444"},
        headers=admin_headers,
    )
    assert reassigned.status_code == 201
    assert reassigned.json()["restaurant_id"] == second_restaurant
