"""
End-to-end tests for AI-assisted booking through /chat (Stage 2c). Only
the Gemini network call itself is stubbed (simulating what a model
would send after gathering booking details) — everything else is real:
chat.py's tool handler, schemas.BookingCreate validation, and the exact
same app/booking.py create_booking() that admin booking management
uses. This is what proves the tool handler can't itself decide a
booking succeeded, and that a rejected or incomplete call never creates
a database row.

Date allocation: see tests/test_booking_service.py's module docstring
for why other test files use distant, isolated dates. This file's
anchor sits well past tests/test_booking_admin.py's range (that file's
anchor+150, extending to roughly +220) so the two can never share a
date.
"""

import itertools
from datetime import date, timedelta
from types import SimpleNamespace

from app import llm, models

_ANCHOR = date.today() + timedelta(days=250)
_counter = itertools.count(1)


def _fresh_date() -> date:
    return _ANCHOR + timedelta(weeks=next(_counter))


_CLOSED_DATE = _ANCHOR + timedelta(days=1)
_CLOSED_DAY_NAME = _CLOSED_DATE.strftime("%A")

# Within every seeded day's opening hours regardless of weekday — see
# tests/test_booking_service.py's module docstring for the reasoning.
_SAFE_TIME = "13:00"


def _fake_response(text=None, function_calls=None):
    return SimpleNamespace(
        text=text,
        function_calls=function_calls or [],
        candidates=[SimpleNamespace(content=SimpleNamespace(role="model", parts=[]))],
    )


def _fake_call(args):
    return SimpleNamespace(name="create_booking", args=args)


def _stub_tool_round_trip(monkeypatch, call_args, final_text):
    """Simulates a model that calls create_booking on its first turn,
    then produces final_text once it sees the tool's result."""
    calls = {"n": 0}

    def fake_generate_content(model, contents, config):
        calls["n"] += 1
        if calls["n"] == 1:
            return _fake_response(function_calls=[_fake_call(call_args)])
        return _fake_response(text=final_text)

    monkeypatch.setattr(llm.client.models, "generate_content", fake_generate_content)


def _booking_count(db, restaurant_id=1):
    return db.query(models.Booking).filter(models.Booking.restaurant_id == restaurant_id).count()


def test_plain_chat_without_tool_call_is_unaffected(client, monkeypatch, db):
    def fake_generate_content(model, contents, config):
        return _fake_response(text="Hiya! How can I help?")

    monkeypatch.setattr(llm.client.models, "generate_content", fake_generate_content)
    before = _booking_count(db)

    response = client.post("/chat", json={"message": "hi", "history": [], "restaurant_id": 1})

    assert response.status_code == 200
    assert response.json() == {"reply": "Hiya! How can I help?"}
    assert _booking_count(db) == before


def test_successful_booking_creates_a_real_row(client, monkeypatch, db):
    d = _fresh_date()
    args = {
        "customer_name": "Chat Customer",
        "phone": "01234 999999",
        "email": "chat-customer@example.com",
        "booking_date": d.isoformat(),
        "booking_time": _SAFE_TIME,
        "party_size": 3,
    }
    _stub_tool_round_trip(monkeypatch, args, "Lovely, you're all booked in!")
    before = _booking_count(db)

    response = client.post(
        "/chat", json={"message": "Book me a table", "history": [], "restaurant_id": 1}
    )

    assert response.status_code == 200
    assert response.json() == {"reply": "Lovely, you're all booked in!"}
    assert _booking_count(db) == before + 1

    created = (
        db.query(models.Booking)
        .filter(models.Booking.restaurant_id == 1, models.Booking.booking_date == d)
        .one()
    )
    assert created.customer_name == "Chat Customer"
    assert created.party_size == 3
    assert created.status == "confirmed"


def test_missing_required_field_is_rejected_without_creating_a_booking(client, monkeypatch, db):
    incomplete_args = {
        "customer_name": "No Email Person",
        "phone": "01234 000000",
        # email deliberately omitted
        "booking_date": _fresh_date().isoformat(),
        "booking_time": _SAFE_TIME,
        "party_size": 2,
    }
    _stub_tool_round_trip(monkeypatch, incomplete_args, "Could I grab your email address too?")
    before = _booking_count(db)

    response = client.post(
        "/chat", json={"message": "Book me a table", "history": [], "restaurant_id": 1}
    )

    assert response.status_code == 200
    assert response.json() == {"reply": "Could I grab your email address too?"}
    assert _booking_count(db) == before


def test_capacity_rejection_does_not_create_a_booking(client, monkeypatch, db, admin_headers):
    d = _fresh_date()
    capacity = client.get("/admin/restaurant/1", headers=admin_headers).json()["seating_capacity"]
    half = capacity // 2

    # Fill capacity directly via the admin API first (split across two
    # bookings since a single booking is capped at 20 people).
    for i in range(2):
        client.post(
            "/admin/restaurant/1/bookings",
            json={
                "customer_name": f"Big Group {i}",
                "phone": "01234 111111",
                "email": f"biggroup{i}@example.com",
                "booking_date": d.isoformat(),
                "booking_time": _SAFE_TIME,
                "party_size": half,
            },
            headers=admin_headers,
        )
    before = _booking_count(db)

    args = {
        "customer_name": "Latecomer",
        "phone": "01234 222222",
        "email": "latecomer@example.com",
        "booking_date": d.isoformat(),
        "booking_time": _SAFE_TIME,
        "party_size": 1,
    }
    _stub_tool_round_trip(monkeypatch, args, "Sorry, we're fully booked at that time.")

    response = client.post(
        "/chat", json={"message": "Book a table for 1", "history": [], "restaurant_id": 1}
    )

    assert response.status_code == 200
    assert response.json() == {"reply": "Sorry, we're fully booked at that time."}
    assert _booking_count(db) == before  # the chat attempt added nothing


def test_closed_day_rejection_does_not_create_a_booking(client, monkeypatch, db):
    restaurant = db.query(models.Restaurant).first()
    hours = (
        db.query(models.OpeningHours)
        .filter(
            models.OpeningHours.restaurant_id == restaurant.id,
            models.OpeningHours.day_of_week == _CLOSED_DAY_NAME,
        )
        .first()
    )
    original_is_closed = hours.is_closed
    hours.is_closed = True
    db.commit()
    try:
        before = _booking_count(db)
        args = {
            "customer_name": "Closed Day Customer",
            "phone": "01234 333333",
            "email": "closedday@example.com",
            "booking_date": _CLOSED_DATE.isoformat(),
            "booking_time": _SAFE_TIME,
            "party_size": 2,
        }
        _stub_tool_round_trip(monkeypatch, args, "Ah, we're closed that day, I'm afraid.")

        response = client.post(
            "/chat", json={"message": "Book me in", "history": [], "restaurant_id": 1}
        )

        assert response.status_code == 200
        assert response.json() == {"reply": "Ah, we're closed that day, I'm afraid."}
        assert _booking_count(db) == before
    finally:
        hours.is_closed = original_is_closed
        db.commit()


def test_tool_round_trip_failure_falls_back_to_generic_500(client, monkeypatch):
    call_count = {"n": 0}

    def fake_generate_content(model, contents, config):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return _fake_response(
                function_calls=[
                    _fake_call(
                        {
                            "customer_name": "Whoever",
                            "phone": "01234 444444",
                            "email": "whoever@example.com",
                            "booking_date": _fresh_date().isoformat(),
                            "booking_time": _SAFE_TIME,
                            "party_size": 2,
                        }
                    )
                ]
            )
        raise RuntimeError("simulated upstream failure on the second Gemini call")

    monkeypatch.setattr(llm.client.models, "generate_content", fake_generate_content)

    response = client.post(
        "/chat", json={"message": "Book me in", "history": [], "restaurant_id": 1}
    )

    assert response.status_code == 500
    assert response.json() == {
        "detail": "Sorry, something went wrong on our end. Please try again shortly."
    }


def test_chat_driven_booking_does_not_log_pii(client, monkeypatch):
    from app.logging_config import LOG_FILE

    d = _fresh_date()
    args = {
        "customer_name": "Private Person",
        "phone": "01234 555555",
        "email": "private-person@example.com",
        "booking_date": d.isoformat(),
        "booking_time": _SAFE_TIME,
        "party_size": 2,
    }
    _stub_tool_round_trip(monkeypatch, args, "You're booked in!")

    client.post("/chat", json={"message": "Book me a table", "history": [], "restaurant_id": 1})

    content = LOG_FILE.read_text(encoding="utf-8")
    assert "Private Person" not in content
    assert "private-person@example.com" not in content
    assert "01234 555555" not in content
