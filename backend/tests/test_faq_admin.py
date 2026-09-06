"""
Admin FAQ CRUD endpoints (Stage 3 Step 4: restaurant onboarding
completeness). Same auth/rate-limit protection as every other
/admin/* route (inherited via get_current_admin + require_restaurant_access
declared per-handler — see routers/admin.py), plus HTTP-level
create/list/update/delete behaviour and restaurant-id scoping.
"""

import pytest


def _faq_payload(**overrides):
    payload = {"question": "Do you have parking?", "answer": "Yes, free parking on-site."}
    payload.update(overrides)
    return payload


def test_list_faqs_requires_admin_key(client):
    response = client.get("/admin/restaurant/1/faqs")
    assert response.status_code == 401


def test_list_faqs_with_admin_key_succeeds(client, admin_headers):
    response = client.get("/admin/restaurant/1/faqs", headers=admin_headers)
    assert response.status_code == 200
    assert isinstance(response.json(), list)


def test_create_faq_requires_admin_key(client):
    response = client.post("/admin/restaurant/1/faqs", json=_faq_payload())
    assert response.status_code == 401


def test_create_faq_with_admin_key_succeeds(client, admin_headers):
    response = client.post("/admin/restaurant/1/faqs", json=_faq_payload(), headers=admin_headers)
    assert response.status_code == 201
    body = response.json()["faq"]
    assert body["question"] == "Do you have parking?"
    assert body["answer"] == "Yes, free parking on-site."
    assert body["restaurant_id"] == 1


def test_created_faq_appears_in_list(client, admin_headers):
    created = client.post(
        "/admin/restaurant/1/faqs", json=_faq_payload(question="Unique question marker?"), headers=admin_headers
    ).json()["faq"]

    listed = client.get("/admin/restaurant/1/faqs", headers=admin_headers).json()
    assert any(f["id"] == created["id"] and f["question"] == "Unique question marker?" for f in listed)


@pytest.mark.parametrize("payload", [
    {"question": "", "answer": "Something"},
    {"question": "Something", "answer": ""},
    {"question": "x" * 501, "answer": "Something"},
    {"question": "Something", "answer": "x" * 2001},
])
def test_create_faq_rejects_invalid_payloads(client, admin_headers, payload):
    response = client.post("/admin/restaurant/1/faqs", json=payload, headers=admin_headers)
    assert response.status_code == 422


def test_update_faq_requires_admin_key(client, admin_headers):
    created = client.post("/admin/restaurant/1/faqs", json=_faq_payload(), headers=admin_headers).json()["faq"]
    response = client.patch(f"/admin/restaurant/1/faqs/{created['id']}", json={"answer": "Updated answer"})
    assert response.status_code == 401


def test_update_faq_with_admin_key_succeeds(client, admin_headers):
    created = client.post("/admin/restaurant/1/faqs", json=_faq_payload(), headers=admin_headers).json()["faq"]
    response = client.patch(
        f"/admin/restaurant/1/faqs/{created['id']}", json={"answer": "Updated answer"}, headers=admin_headers
    )
    assert response.status_code == 200
    assert response.json()["faq"]["answer"] == "Updated answer"
    assert response.json()["faq"]["question"] == created["question"]  # unset field untouched


def test_update_faq_not_found(client, admin_headers):
    response = client.patch(
        "/admin/restaurant/1/faqs/999999", json={"answer": "x"}, headers=admin_headers
    )
    assert response.status_code == 404


def test_delete_faq_requires_admin_key(client, admin_headers):
    created = client.post("/admin/restaurant/1/faqs", json=_faq_payload(), headers=admin_headers).json()["faq"]
    response = client.delete(f"/admin/restaurant/1/faqs/{created['id']}")
    assert response.status_code == 401


def test_delete_faq_with_admin_key_succeeds(client, admin_headers):
    created = client.post("/admin/restaurant/1/faqs", json=_faq_payload(), headers=admin_headers).json()["faq"]
    response = client.delete(f"/admin/restaurant/1/faqs/{created['id']}", headers=admin_headers)
    assert response.status_code == 200
    assert response.json()["faq_id"] == created["id"]

    listed = client.get("/admin/restaurant/1/faqs", headers=admin_headers).json()
    assert all(f["id"] != created["id"] for f in listed)


def test_delete_faq_not_found(client, admin_headers):
    response = client.delete("/admin/restaurant/1/faqs/999999", headers=admin_headers)
    assert response.status_code == 404


def test_faq_endpoints_404_for_unknown_restaurant(client, admin_headers):
    assert client.get("/admin/restaurant/999999/faqs", headers=admin_headers).status_code == 404
    assert client.post(
        "/admin/restaurant/999999/faqs", json=_faq_payload(), headers=admin_headers
    ).status_code == 404
