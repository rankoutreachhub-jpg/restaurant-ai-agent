"""
Admin booking endpoints: same auth/rate-limit protection as every other
/admin/* route (inherited automatically from the shared router
dependencies — see routers/bookings.py), plus HTTP-level create/list/
update/delete and conflict (409) behaviour.

Date allocation: see test_booking_service.py's module docstring — this
file uses its own anchor, far enough from that file's range that the
two can never share a calendar date.
"""

import itertools
from datetime import date, timedelta

_ANCHOR = date.today() + timedelta(days=150)
_counter = itertools.count(1)


def _fresh_date_str() -> str:
    return (_ANCHOR + timedelta(weeks=next(_counter))).isoformat()


def _booking_payload(**overrides):
    payload = {
        "customer_name": "Jane Doe",
        "phone": "01234 567890",
        "email": "jane@example.com",
        "booking_date": _fresh_date_str(),
        "booking_time": "12:00",
        "party_size": 4,
    }
    payload.update(overrides)
    return payload


def _get_capacity(client, admin_headers) -> int:
    response = client.get("/admin/restaurant/1", headers=admin_headers)
    return response.json()["seating_capacity"]


def test_list_bookings_requires_admin_key(client):
    response = client.get("/admin/restaurant/1/bookings")
    assert response.status_code == 401


def test_create_booking_requires_admin_key(client):
    response = client.post("/admin/restaurant/1/bookings", json=_booking_payload())
    assert response.status_code == 401


def test_create_and_list_booking_with_admin_key(client, admin_headers):
    create_response = client.post(
        "/admin/restaurant/1/bookings", json=_booking_payload(), headers=admin_headers
    )
    assert create_response.status_code == 201
    body = create_response.json()
    assert body["customer_name"] == "Jane Doe"
    assert body["status"] == "confirmed"

    list_response = client.get("/admin/restaurant/1/bookings", headers=admin_headers)
    assert list_response.status_code == 200
    assert any(b["id"] == body["id"] for b in list_response.json())


def test_create_booking_returns_404_for_unknown_restaurant(client, admin_headers):
    response = client.post(
        "/admin/restaurant/999999/bookings", json=_booking_payload(), headers=admin_headers
    )
    assert response.status_code == 404


def test_create_booking_returns_422_for_past_date(client, admin_headers):
    past_date = (date.today() - timedelta(days=1)).isoformat()
    response = client.post(
        "/admin/restaurant/1/bookings",
        json=_booking_payload(booking_date=past_date),
        headers=admin_headers,
    )
    assert response.status_code == 422


def test_create_booking_returns_422_for_invalid_email(client, admin_headers):
    response = client.post(
        "/admin/restaurant/1/bookings",
        json=_booking_payload(email="not-an-email"),
        headers=admin_headers,
    )
    assert response.status_code == 422


def test_create_booking_returns_409_when_over_capacity(client, admin_headers):
    capacity = _get_capacity(client, admin_headers)
    half = capacity // 2
    d = _fresh_date_str()

    client.post(
        "/admin/restaurant/1/bookings",
        json=_booking_payload(booking_date=d, party_size=half, booking_time="20:00"),
        headers=admin_headers,
    )
    client.post(
        "/admin/restaurant/1/bookings",
        json=_booking_payload(booking_date=d, party_size=capacity - half, booking_time="20:15"),
        headers=admin_headers,
    )

    response = client.post(
        "/admin/restaurant/1/bookings",
        json=_booking_payload(booking_date=d, party_size=1, booking_time="20:30"),
        headers=admin_headers,
    )
    assert response.status_code == 409


def test_get_booking_not_found(client, admin_headers):
    response = client.get("/admin/restaurant/1/bookings/999999", headers=admin_headers)
    assert response.status_code == 404


def test_update_booking_status_to_cancelled(client, admin_headers):
    created = client.post(
        "/admin/restaurant/1/bookings", json=_booking_payload(), headers=admin_headers
    ).json()

    response = client.patch(
        f"/admin/restaurant/1/bookings/{created['id']}",
        json={"status": "cancelled"},
        headers=admin_headers,
    )
    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"


def test_update_booking_requires_admin_key(client, admin_headers):
    created = client.post(
        "/admin/restaurant/1/bookings", json=_booking_payload(), headers=admin_headers
    ).json()

    response = client.patch(f"/admin/restaurant/1/bookings/{created['id']}", json={"status": "cancelled"})
    assert response.status_code == 401


def test_delete_booking_requires_admin_key(client, admin_headers):
    created = client.post(
        "/admin/restaurant/1/bookings", json=_booking_payload(), headers=admin_headers
    ).json()

    response = client.delete(f"/admin/restaurant/1/bookings/{created['id']}")
    assert response.status_code == 401


def test_delete_booking_with_admin_key(client, admin_headers):
    created = client.post(
        "/admin/restaurant/1/bookings", json=_booking_payload(), headers=admin_headers
    ).json()

    response = client.delete(f"/admin/restaurant/1/bookings/{created['id']}", headers=admin_headers)
    assert response.status_code == 200

    get_response = client.get(f"/admin/restaurant/1/bookings/{created['id']}", headers=admin_headers)
    assert get_response.status_code == 404


def test_list_bookings_filters_by_status(client, admin_headers):
    d = _fresh_date_str()
    created = client.post(
        "/admin/restaurant/1/bookings",
        json=_booking_payload(booking_date=d, booking_time="17:00"),
        headers=admin_headers,
    ).json()
    client.patch(
        f"/admin/restaurant/1/bookings/{created['id']}",
        json={"status": "cancelled"},
        headers=admin_headers,
    )

    cancelled = client.get(
        "/admin/restaurant/1/bookings", params={"status": "cancelled"}, headers=admin_headers
    ).json()
    assert any(b["id"] == created["id"] for b in cancelled)

    confirmed = client.get(
        "/admin/restaurant/1/bookings", params={"status": "confirmed"}, headers=admin_headers
    ).json()
    assert all(b["id"] != created["id"] for b in confirmed)
