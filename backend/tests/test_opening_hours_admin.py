"""
Admin opening-hours CREATE endpoint (Stage 3 Step 4: restaurant
onboarding completeness). The existing GET/PATCH endpoints only ever
read or update an existing day; this is the only way to create the
initial rows for a newly onboarded restaurant. Uses the platform-admin
endpoint to onboard a bare restaurant (no seeded opening hours) so
these tests exercise true "from scratch" creation, not restaurant 1's
already-seeded week.
"""


def _new_bare_restaurant(client, admin_headers) -> int:
    response = client.post(
        "/admin/platform/restaurants",
        json={
            "name": "The Onboarding Test Restaurant",
            "address": "1 Test Street",
            "phone": "0000 000000",
            "email": "test@example.com",
            "seating_capacity": 20,
        },
        headers=admin_headers,
    )
    assert response.status_code == 201
    return response.json()["restaurant"]["id"]


def _day(name, open_time="12:00", close_time="22:00", is_closed=False):
    return {"day_of_week": name, "open_time": open_time, "close_time": close_time, "is_closed": is_closed}


def test_create_opening_hours_requires_admin_key(client, admin_headers):
    restaurant_id = _new_bare_restaurant(client, admin_headers)
    response = client.post(
        f"/admin/restaurant/{restaurant_id}/opening-hours", json={"days": [_day("Monday")]}
    )
    assert response.status_code == 401


def test_create_single_day(client, admin_headers):
    restaurant_id = _new_bare_restaurant(client, admin_headers)
    response = client.post(
        f"/admin/restaurant/{restaurant_id}/opening-hours",
        json={"days": [_day("Monday")]},
        headers=admin_headers,
    )
    assert response.status_code == 201
    created = response.json()["opening_hours"]
    assert len(created) == 1
    assert created[0]["day_of_week"] == "Monday"
    assert created[0]["restaurant_id"] == restaurant_id


def test_create_all_seven_days_in_one_call(client, admin_headers):
    restaurant_id = _new_bare_restaurant(client, admin_headers)
    days = [
        _day(name) for name in
        ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    ]
    response = client.post(
        f"/admin/restaurant/{restaurant_id}/opening-hours", json={"days": days}, headers=admin_headers
    )
    assert response.status_code == 201
    assert len(response.json()["opening_hours"]) == 7

    listed = client.get(f"/admin/restaurant/{restaurant_id}/opening-hours", headers=admin_headers).json()
    assert len(listed) == 7


def test_create_closed_day_without_times(client, admin_headers):
    restaurant_id = _new_bare_restaurant(client, admin_headers)
    response = client.post(
        f"/admin/restaurant/{restaurant_id}/opening-hours",
        json={"days": [{"day_of_week": "Sunday", "is_closed": True}]},
        headers=admin_headers,
    )
    assert response.status_code == 201
    assert response.json()["opening_hours"][0]["is_closed"] is True


def test_create_open_day_missing_times_is_rejected(client, admin_headers):
    restaurant_id = _new_bare_restaurant(client, admin_headers)
    response = client.post(
        f"/admin/restaurant/{restaurant_id}/opening-hours",
        json={"days": [{"day_of_week": "Monday", "is_closed": False}]},
        headers=admin_headers,
    )
    assert response.status_code == 422


def test_create_rejects_invalid_time_format(client, admin_headers):
    restaurant_id = _new_bare_restaurant(client, admin_headers)
    response = client.post(
        f"/admin/restaurant/{restaurant_id}/opening-hours",
        json={"days": [_day("Monday", open_time="not-a-time")]},
        headers=admin_headers,
    )
    assert response.status_code == 422


def test_create_rejects_invalid_day_name(client, admin_headers):
    restaurant_id = _new_bare_restaurant(client, admin_headers)
    response = client.post(
        f"/admin/restaurant/{restaurant_id}/opening-hours",
        json={"days": [_day("Someday")]},
        headers=admin_headers,
    )
    assert response.status_code == 422


def test_create_rejects_duplicate_day_within_same_request(client, admin_headers):
    restaurant_id = _new_bare_restaurant(client, admin_headers)
    response = client.post(
        f"/admin/restaurant/{restaurant_id}/opening-hours",
        json={"days": [_day("Monday"), _day("Monday", open_time="13:00", close_time="23:00")]},
        headers=admin_headers,
    )
    assert response.status_code == 422


def test_create_rejects_more_than_seven_days(client, admin_headers):
    restaurant_id = _new_bare_restaurant(client, admin_headers)
    # 8 entries can't all be distinct real weekday names, so this also
    # exercises the max_length cap independently of the duplicate check.
    days = [_day(name) for name in
            ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]]
    days.append(_day("Monday", open_time="13:00", close_time="23:00"))
    response = client.post(
        f"/admin/restaurant/{restaurant_id}/opening-hours", json={"days": days}, headers=admin_headers
    )
    assert response.status_code == 422


def test_create_conflicts_with_already_existing_day(client, admin_headers):
    restaurant_id = _new_bare_restaurant(client, admin_headers)
    first = client.post(
        f"/admin/restaurant/{restaurant_id}/opening-hours",
        json={"days": [_day("Monday")]},
        headers=admin_headers,
    )
    assert first.status_code == 201

    second = client.post(
        f"/admin/restaurant/{restaurant_id}/opening-hours",
        json={"days": [_day("Monday", open_time="09:00", close_time="17:00")]},
        headers=admin_headers,
    )
    assert second.status_code == 409
    assert "Monday" in second.json()["detail"]

    # Confirm no duplicate row was created and the original values stand.
    listed = client.get(f"/admin/restaurant/{restaurant_id}/opening-hours", headers=admin_headers).json()
    mondays = [h for h in listed if h["day_of_week"] == "Monday"]
    assert len(mondays) == 1
    assert mondays[0]["open_time"] == "12:00"


def test_create_partial_conflict_rejects_whole_request(client, admin_headers):
    """If even one of several requested days already exists, the whole
    request is rejected (no partial creation) — the caller gets a clear
    signal rather than silently-partial state."""
    restaurant_id = _new_bare_restaurant(client, admin_headers)
    client.post(
        f"/admin/restaurant/{restaurant_id}/opening-hours",
        json={"days": [_day("Monday")]},
        headers=admin_headers,
    )

    response = client.post(
        f"/admin/restaurant/{restaurant_id}/opening-hours",
        json={"days": [_day("Tuesday"), _day("Monday", open_time="09:00", close_time="17:00")]},
        headers=admin_headers,
    )
    assert response.status_code == 409

    listed = client.get(f"/admin/restaurant/{restaurant_id}/opening-hours", headers=admin_headers).json()
    # Tuesday must NOT have been created either, since the request failed as a whole.
    assert not any(h["day_of_week"] == "Tuesday" for h in listed)


def test_create_opening_hours_404_for_unknown_restaurant(client, admin_headers):
    response = client.post(
        "/admin/restaurant/999999/opening-hours", json={"days": [_day("Monday")]}, headers=admin_headers
    )
    assert response.status_code == 404


def test_duplicate_opening_hours_row_is_unique_at_the_database_level(client, admin_headers):
    """
    Defense in depth: even if application code ever forgot the
    check-before-insert in the create endpoint, the database itself
    must refuse a duplicate (restaurant_id, day_of_week) row — this is
    exactly the race-condition backstop the endpoint's IntegrityError
    handling relies on.
    """
    import pytest
    from sqlalchemy.exc import IntegrityError

    from app import models
    from app.database import SessionLocal

    restaurant_id = _new_bare_restaurant(client, admin_headers)

    session = SessionLocal()
    try:
        session.add(models.OpeningHours(
            restaurant_id=restaurant_id, day_of_week="Monday",
            open_time="12:00", close_time="22:00", is_closed=False,
        ))
        session.commit()

        session.add(models.OpeningHours(
            restaurant_id=restaurant_id, day_of_week="Monday",
            open_time="09:00", close_time="17:00", is_closed=False,
        ))
        with pytest.raises(IntegrityError):
            session.commit()
    finally:
        session.rollback()
        session.close()


def test_existing_day_can_still_be_updated_via_patch(client, admin_headers):
    """Regression: the new CREATE endpoint must not interfere with the
    existing UPDATE endpoint for a day that already exists."""
    restaurant_id = _new_bare_restaurant(client, admin_headers)
    client.post(
        f"/admin/restaurant/{restaurant_id}/opening-hours",
        json={"days": [_day("Monday")]},
        headers=admin_headers,
    )
    response = client.patch(
        f"/admin/restaurant/{restaurant_id}/opening-hours/Monday",
        json={"close_time": "23:00"},
        headers=admin_headers,
    )
    assert response.status_code == 200
    assert response.json()["opening_hours"]["close_time"] == "23:00"
