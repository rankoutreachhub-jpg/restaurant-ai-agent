"""
POST /widget/{widget_key}/chat (Stage 4 Phase C): the public,
unauthenticated widget chat endpoint. Mirrors tests/test_chat_persistence.py
and tests/test_whatsapp_booking.py's patterns closely — same stubbing
style for the Gemini call, same booking-tool-round-trip helpers — since
this endpoint deliberately reuses the exact same underlying pipeline.

Date allocation: see tests/test_booking_service.py's module docstring
for the overall convention. This file's anchor (+335) sits past every
other file's range (test_whatsapp_booking.py's is the furthest out,
using up to +345) while its own furthest use (+335+21=356) stays safely
under schemas.BOOKING_MAX_ADVANCE_DAYS (365) — a real rejection an
earlier, too-large anchor (+380, already past the 365 cap by itself)
hit in this file before the anchor was corrected.
"""

import itertools
from datetime import date, timedelta
from types import SimpleNamespace

from app import config, models
from app import llm as llm_module
from app.rate_limit import chat_rate_limiter, widget_chat_rate_limiter

_ANCHOR = date.today() + timedelta(days=335)
_counter = itertools.count(1)


def _fresh_date() -> date:
    return _ANCHOR + timedelta(weeks=next(_counter))


_SAFE_TIME = "13:00"


def _chat_url(widget_key):
    return f"/widget/{widget_key}/chat"


def _create_widget_config(client, admin_headers, restaurant_id, **fields):
    response = client.post(
        f"/admin/restaurant/{restaurant_id}/widget-config",
        json=fields,
        headers=admin_headers,
    )
    assert response.status_code == 201
    return response.json()


def _fake_response(text=None, function_calls=None):
    return SimpleNamespace(
        text=text,
        function_calls=function_calls or [],
        candidates=[SimpleNamespace(content=SimpleNamespace(role="model", parts=[]))],
    )


def _fake_call(args):
    return SimpleNamespace(name="create_booking", args=args)


def _stub_plain_reply(monkeypatch, text, capture=None):
    def fake_generate_content(model, contents, config):
        if capture is not None:
            capture.append(contents)
        return _fake_response(text=text)

    monkeypatch.setattr(llm_module.client.models, "generate_content", fake_generate_content)


def _stub_tool_round_trip(monkeypatch, call_args, final_text):
    calls = {"n": 0}

    def fake_generate_content(model, contents, config):
        calls["n"] += 1
        if calls["n"] == 1:
            return _fake_response(function_calls=[_fake_call(call_args)])
        return _fake_response(text=final_text)

    monkeypatch.setattr(llm_module.client.models, "generate_content", fake_generate_content)


def _conversations_for(db, restaurant_id, channel="widget"):
    return (
        db.query(models.Conversation)
        .filter(models.Conversation.restaurant_id == restaurant_id, models.Conversation.channel == channel)
        .all()
    )


# --- Valid chat / no-auth access ---

def test_valid_widget_chat_succeeds_with_no_auth_headers(client, monkeypatch, admin_headers, db):
    created = _create_widget_config(client, admin_headers, 1)
    _stub_plain_reply(monkeypatch, "Hiya! How can I help?")

    response = client.post(_chat_url(created["widget_key"]), json={"message": "hi"})
    assert response.status_code == 200
    assert response.json()["reply"] == "Hiya! How can I help?"
    assert "X-Conversation-Token" in response.headers


def test_valid_chat_persists_the_turn_with_widget_channel(client, monkeypatch, admin_headers, second_restaurant, db):
    created = _create_widget_config(client, admin_headers, second_restaurant)
    _stub_plain_reply(monkeypatch, "Hiya!")

    response = client.post(_chat_url(created["widget_key"]), json={"message": "hi"})
    token = response.headers["X-Conversation-Token"]

    db.expire_all()
    conversation = db.query(models.Conversation).filter(models.Conversation.public_token == token).one()
    assert conversation.channel == "widget"
    assert conversation.restaurant_id == second_restaurant

    messages = (
        db.query(models.Message)
        .filter(models.Message.conversation_id == conversation.id)
        .order_by(models.Message.created_at)
        .all()
    )
    assert [m.role for m in messages] == ["user", "assistant"]
    assert messages[0].content == "hi"
    assert messages[1].content == "Hiya!"


# --- Unknown / inactive widget: identical generic 404 ---

def test_unknown_widget_key_returns_404(client):
    response = client.post(_chat_url("wgt_never_issued"), json={"message": "hi"})
    assert response.status_code == 404


def test_inactive_widget_returns_404(client, admin_headers, second_restaurant):
    created = _create_widget_config(client, admin_headers, second_restaurant, is_active=False)
    response = client.post(_chat_url(created["widget_key"]), json={"message": "hi"})
    assert response.status_code == 404


def test_unknown_and_inactive_widget_chat_return_identical_404(client, admin_headers, second_restaurant):
    inactive = _create_widget_config(client, admin_headers, second_restaurant, is_active=False)

    unknown_response = client.post(_chat_url("wgt_never_issued_00000"), json={"message": "hi"})
    inactive_response = client.post(_chat_url(inactive["widget_key"]), json={"message": "hi"})

    assert unknown_response.status_code == inactive_response.status_code == 404
    assert unknown_response.json() == inactive_response.json()


def test_widget_config_404_matches_widget_chat_404(client, admin_headers, second_restaurant):
    """The config endpoint (Phase B) and the chat endpoint (Phase C)
    present one consistent 'this widget isn't reachable' surface."""
    config_response = client.get(f"/widget/wgt_never_issued/config")
    chat_response = client.post(_chat_url("wgt_never_issued"), json={"message": "hi"})
    assert config_response.status_code == chat_response.status_code == 404
    assert config_response.json() == chat_response.json()


# --- Conversation creation / resume ---

def test_first_message_creates_a_new_conversation(client, monkeypatch, admin_headers, second_restaurant, db):
    created = _create_widget_config(client, admin_headers, second_restaurant)
    _stub_plain_reply(monkeypatch, "Hiya!")
    before = len(_conversations_for(db, second_restaurant))

    response = client.post(_chat_url(created["widget_key"]), json={"message": "hi"})
    assert response.status_code == 200
    assert len(_conversations_for(db, second_restaurant)) == before + 1


def test_second_message_with_token_resumes_the_same_conversation(
    client, monkeypatch, admin_headers, second_restaurant, db
):
    created = _create_widget_config(client, admin_headers, second_restaurant)
    _stub_plain_reply(monkeypatch, "Nice to meet you!")

    first = client.post(
        _chat_url(created["widget_key"]),
        json={"message": "My favourite colour is chartreuse"},
    )
    token = first.headers["X-Conversation-Token"]

    captured = []
    _stub_plain_reply(monkeypatch, "Got it!", capture=captured)
    second = client.post(
        _chat_url(created["widget_key"]),
        json={"message": "What did I just tell you?"},
        headers={"X-Conversation-Token": token},
    )
    assert second.status_code == 200
    assert second.headers["X-Conversation-Token"] == token

    sent_texts = [part.text for turn in captured[0] for part in turn.parts]
    assert any("chartreuse" in t for t in sent_texts)


# --- Invalid/mismatched conversation token ---

def test_unknown_token_silently_starts_a_new_conversation(client, monkeypatch, admin_headers, second_restaurant):
    created = _create_widget_config(client, admin_headers, second_restaurant)
    _stub_plain_reply(monkeypatch, "Hiya!")

    response = client.post(
        _chat_url(created["widget_key"]),
        json={"message": "hi"},
        headers={"X-Conversation-Token": "not-a-real-token"},
    )
    assert response.status_code == 200
    assert response.headers["X-Conversation-Token"] != "not-a-real-token"


def test_token_from_a_different_widget_does_not_resume_or_leak(client, monkeypatch, admin_headers, second_restaurant):
    config_1 = _create_widget_config(client, admin_headers, 1)
    config_2 = _create_widget_config(client, admin_headers, second_restaurant)

    _stub_plain_reply(monkeypatch, "Reply for restaurant 1")
    first = client.post(_chat_url(config_1["widget_key"]), json={"message": "Secret restaurant-1 detail"})
    token_for_restaurant_1 = first.headers["X-Conversation-Token"]

    captured = []
    _stub_plain_reply(monkeypatch, "Reply for restaurant 2", capture=captured)
    second = client.post(
        _chat_url(config_2["widget_key"]),
        json={"message": "hi"},
        headers={"X-Conversation-Token": token_for_restaurant_1},
    )
    assert second.status_code == 200
    new_token = second.headers["X-Conversation-Token"]
    assert new_token != token_for_restaurant_1

    sent_texts = [part.text for turn in captured[0] for part in turn.parts]
    assert not any("Secret restaurant-1 detail" in t for t in sent_texts)


# --- Tenant isolation ---

def test_two_widgets_never_cross_contaminate_gemini_context(client, monkeypatch, admin_headers, second_restaurant):
    config_1 = _create_widget_config(client, admin_headers, 1)
    config_2 = _create_widget_config(client, admin_headers, second_restaurant)

    captured_context = []

    def fake_generate_content(model, contents, config):
        captured_context.append(config.system_instruction)
        return _fake_response(text="reply")

    monkeypatch.setattr(llm_module.client.models, "generate_content", fake_generate_content)

    client.post(_chat_url(config_1["widget_key"]), json={"message": "hi"})
    client.post(_chat_url(config_2["widget_key"]), json={"message": "hi"})

    assert "The Anchor" not in captured_context[0]
    assert "The Kings Arms" not in captured_context[1]


# --- Gemini failure behavior ---

def test_booking_succeeds_but_final_reply_failure_does_not_persist_the_turn(
    client, monkeypatch, admin_headers, second_restaurant, db
):
    created = _create_widget_config(client, admin_headers, second_restaurant)
    d = _fresh_date()
    call_count = {"n": 0}

    def fake_generate_content(model, contents, config):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return _fake_response(function_calls=[_fake_call({
                "customer_name": "Widget Edge Case",
                "phone": "07911 000222",
                "email": "widget-edge-case@example.com",
                "booking_date": d.isoformat(),
                "booking_time": _SAFE_TIME,
                "party_size": 2,
            })])
        raise RuntimeError("simulated upstream failure on the second Gemini call")

    monkeypatch.setattr(llm_module.client.models, "generate_content", fake_generate_content)

    response = client.post(_chat_url(created["widget_key"]), json={"message": "Book me a table"})
    assert response.status_code == 500

    booking = (
        db.query(models.Booking)
        .filter(models.Booking.restaurant_id == second_restaurant, models.Booking.booking_date == d)
        .one()
    )
    assert booking.customer_name == "Widget Edge Case"

    db.expire_all()
    messages = db.query(models.Message).join(models.Conversation).filter(
        models.Conversation.restaurant_id == second_restaurant,
        models.Conversation.channel == "widget",
    ).all()
    assert not any(m.content == "Book me a table" for m in messages)


def test_plain_gemini_failure_returns_500_and_persists_nothing(client, monkeypatch, admin_headers, second_restaurant, db):
    created = _create_widget_config(client, admin_headers, second_restaurant)

    def _boom(model, contents, config):
        raise RuntimeError("simulated Gemini outage")

    monkeypatch.setattr(llm_module.client.models, "generate_content", _boom)
    before = db.query(models.Message).count()

    response = client.post(_chat_url(created["widget_key"]), json={"message": "hi"})
    assert response.status_code == 500

    db.expire_all()
    assert db.query(models.Message).count() == before


# --- Booking tool / function calling ---

def test_successful_booking_through_the_widget(client, monkeypatch, admin_headers, second_restaurant, db):
    created = _create_widget_config(client, admin_headers, second_restaurant)
    d = _fresh_date()
    args = {
        "customer_name": "Widget Customer",
        "phone": "07911 000111",
        "email": "widget-customer@example.com",
        "booking_date": d.isoformat(),
        "booking_time": _SAFE_TIME,
        "party_size": 2,
    }
    _stub_tool_round_trip(monkeypatch, args, "You're booked in!")

    response = client.post(_chat_url(created["widget_key"]), json={"message": "Book me a table"})
    assert response.status_code == 200
    assert response.json()["reply"] == "You're booked in!"

    booking = (
        db.query(models.Booking)
        .filter(models.Booking.restaurant_id == second_restaurant, models.Booking.booking_date == d)
        .one()
    )
    assert booking.customer_name == "Widget Customer"
    assert booking.status == "confirmed"


def test_rejected_booking_through_the_widget_creates_no_booking(client, monkeypatch, admin_headers, second_restaurant, db):
    created = _create_widget_config(client, admin_headers, second_restaurant)
    d = _fresh_date()
    from app.booking import create_booking
    from app import schemas as app_schemas

    restaurant = db.query(models.Restaurant).filter(models.Restaurant.id == second_restaurant).first()
    for i in range(2):
        create_booking(db, restaurant, app_schemas.BookingCreate(
            customer_name=f"Filler {i}", phone="0", email=f"filler{i}@example.com",
            booking_date=d, booking_time=_SAFE_TIME, party_size=15,
        ))

    args = {
        "customer_name": "Rejected Widget Customer",
        "phone": "07911 000333",
        "email": "rejected@example.com",
        "booking_date": d.isoformat(),
        "booking_time": _SAFE_TIME,
        "party_size": 5,
    }
    _stub_tool_round_trip(monkeypatch, args, "Sorry, no availability.")

    response = client.post(_chat_url(created["widget_key"]), json={"message": "Book me a table"})
    assert response.status_code == 200

    db.expire_all()
    count = db.query(models.Booking).filter(models.Booking.customer_name == "Rejected Widget Customer").count()
    assert count == 0


def test_booking_disabled_widget_never_offers_the_tool_to_gemini(client, monkeypatch, admin_headers, second_restaurant, db):
    created = _create_widget_config(client, admin_headers, second_restaurant, booking_enabled=False)
    d = _fresh_date()

    captured_tools = []

    def fake_generate_content(model, contents, config):
        captured_tools.append(config.tools)
        return _fake_response(text="I can't take bookings here, sorry!")

    monkeypatch.setattr(llm_module.client.models, "generate_content", fake_generate_content)

    response = client.post(_chat_url(created["widget_key"]), json={
        "message": f"Book me a table for 2 on {d.isoformat()} at {_SAFE_TIME}"
    })
    assert response.status_code == 200
    assert captured_tools == [None]

    db.expire_all()
    assert db.query(models.Booking).filter(models.Booking.restaurant_id == second_restaurant).count() == 0


def test_booking_enabled_widget_does_offer_the_tool(client, monkeypatch, admin_headers, second_restaurant):
    created = _create_widget_config(client, admin_headers, second_restaurant, booking_enabled=True)

    captured_tools = []

    def fake_generate_content(model, contents, config):
        captured_tools.append(config.tools)
        return _fake_response(text="Sure, happy to help you book!")

    monkeypatch.setattr(llm_module.client.models, "generate_content", fake_generate_content)

    client.post(_chat_url(created["widget_key"]), json={"message": "hi"})
    assert captured_tools[0] is not None


# --- Rate limiting ---

def test_customer_is_rate_limited_after_max_messages(client, monkeypatch, admin_headers, second_restaurant):
    created = _create_widget_config(client, admin_headers, second_restaurant)
    _stub_plain_reply(monkeypatch, "reply")

    for _ in range(widget_chat_rate_limiter.max_requests):
        response = client.post(_chat_url(created["widget_key"]), json={"message": "hi"})
        assert response.status_code == 200

    response = client.post(_chat_url(created["widget_key"]), json={"message": "one too many"})
    assert response.status_code == 429


def test_rate_limit_is_isolated_across_different_widgets(client, monkeypatch, admin_headers, second_restaurant):
    """A different restaurant's widget must be completely unaffected by
    another restaurant's traffic hitting its own limit — directly
    testing that one restaurant's traffic can't drain another's quota."""
    config_1 = _create_widget_config(client, admin_headers, 1)
    config_2 = _create_widget_config(client, admin_headers, second_restaurant)
    _stub_plain_reply(monkeypatch, "reply")

    for _ in range(widget_chat_rate_limiter.max_requests):
        client.post(_chat_url(config_1["widget_key"]), json={"message": "hi"})
    assert client.post(_chat_url(config_1["widget_key"]), json={"message": "over"}).status_code == 429

    # A totally different widget (different restaurant) is unaffected.
    response = client.post(_chat_url(config_2["widget_key"]), json={"message": "hi, first message"})
    assert response.status_code == 200


def test_widget_chat_rate_limit_is_independent_of_legacy_chat_rate_limit(client, monkeypatch, admin_headers, second_restaurant):
    """Exhausting legacy /chat's per-IP budget must not affect the
    widget's own budget, and vice versa — proving the two limiters have
    completely independent counters despite TestClient reporting the
    same fake IP for both."""
    created = _create_widget_config(client, admin_headers, second_restaurant)
    monkeypatch.setattr(llm_module, "generate_reply", lambda **kwargs: "stub reply")

    for _ in range(chat_rate_limiter.max_requests):
        response = client.post("/chat", json={"message": "hi", "history": [], "restaurant_id": 1})
        assert response.status_code == 200
    assert client.post("/chat", json={"message": "over", "history": [], "restaurant_id": 1}).status_code == 429

    # The widget's own limiter is untouched by legacy /chat's traffic.
    response = client.post(_chat_url(created["widget_key"]), json={"message": "hi"})
    assert response.status_code == 200
