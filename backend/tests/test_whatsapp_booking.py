"""
AI-assisted booking through WhatsApp (Stage 3 Step 6B), proving it uses
exactly the shared booking tool handler (app/booking_tool.py) — the
same one web /chat uses — via app/whatsapp_processing.py.

Date allocation: see tests/test_booking_service.py's module docstring
for the overall convention. This file's anchor (+310) is offset from
test_chat_persistence.py's (+300) by a non-multiple-of-7 so the two
anchors land on different weekdays and can never collide even where
their ranges numerically overlap. With up to 5 weekly increments used
in this file (max +345 days), it stays safely under
schemas.BOOKING_MAX_ADVANCE_DAYS (365) — a real rejection this file hit
once already when an earlier, too-large anchor pushed a date just past
that cap.
"""

import itertools
from datetime import date, timedelta
from types import SimpleNamespace

from app import config, models
from app import llm as llm_module
from app import whatsapp_client
from app.whatsapp_processing import process_incoming_message

_ANCHOR = date.today() + timedelta(days=310)
_counter = itertools.count(1)


def _fresh_date() -> date:
    return _ANCHOR + timedelta(weeks=next(_counter))


_SAFE_TIME = "13:00"


def _fake_response(text=None, function_calls=None):
    return SimpleNamespace(
        text=text,
        function_calls=function_calls or [],
        candidates=[SimpleNamespace(content=SimpleNamespace(role="model", parts=[]))],
    )


def _fake_call(args):
    return SimpleNamespace(name="create_booking", args=args)


def _text_message(msg_id, from_number, body):
    return {"id": msg_id, "from": from_number, "type": "text", "text": {"body": body}}


def _stub_tool_round_trip(monkeypatch, call_args, final_text):
    calls = {"n": 0}

    def fake_generate_content(model, contents, config):
        calls["n"] += 1
        if calls["n"] == 1:
            return _fake_response(function_calls=[_fake_call(call_args)])
        return _fake_response(text=final_text)

    monkeypatch.setattr(llm_module.client.models, "generate_content", fake_generate_content)


def _stub_send_ok(monkeypatch):
    sent = []
    monkeypatch.setattr(
        whatsapp_client, "send_text_message",
        lambda phone_number_id, to, text: sent.append((phone_number_id, to, text)),
    )
    return sent


def test_successful_booking_through_whatsapp(client, monkeypatch, db, whatsapp_number):
    phone_number_id = whatsapp_number(1)
    d = _fresh_date()
    args = {
        "customer_name": "WhatsApp Customer",
        "phone": "07911 555000",
        "email": "whatsapp-customer@example.com",
        "booking_date": d.isoformat(),
        "booking_time": _SAFE_TIME,
        "party_size": 2,
    }
    _stub_tool_round_trip(monkeypatch, args, "You're booked in!")
    sent = _stub_send_ok(monkeypatch)

    process_incoming_message(phone_number_id, _text_message("wamid.booking.ok", "447911200001", "Book me a table"))

    booking = (
        db.query(models.Booking)
        .filter(models.Booking.restaurant_id == 1, models.Booking.booking_date == d)
        .one()
    )
    assert booking.customer_name == "WhatsApp Customer"
    assert booking.status == "confirmed"
    assert len(sent) == 1
    assert sent[0][2] == "You're booked in!"

    user_message = (
        db.query(models.Message).filter(models.Message.external_message_id == "wamid.booking.ok").one()
    )
    assistant_message = (
        db.query(models.Message)
        .filter(models.Message.conversation_id == user_message.conversation_id, models.Message.role == "assistant")
        .one()
    )
    assert assistant_message.triggered_tool_call is True


def test_rejected_booking_through_whatsapp_creates_no_booking(client, monkeypatch, db, whatsapp_number):
    phone_number_id = whatsapp_number(1)
    d = _fresh_date()
    # Party size of 999 will always be rejected for capacity reasons
    # regardless of the restaurant's seating_capacity.
    args = {
        "customer_name": "Too Many People",
        "phone": "07911 555001",
        "email": "too-many@example.com",
        "booking_date": d.isoformat(),
        "booking_time": _SAFE_TIME,
        "party_size": 20,
    }
    _stub_tool_round_trip(monkeypatch, args, "Sorry, no availability for that many people.")
    sent = _stub_send_ok(monkeypatch)

    bookings_before = db.query(models.Booking).filter(models.Booking.restaurant_id == 1).count()

    # Fill the restaurant to capacity first via real confirmed bookings
    # (BOOKING_MAX_PARTY_SIZE caps a single booking at 20, so two are
    # needed to fill a 40-seat restaurant), so the party-size-20 request
    # above genuinely has no room.
    from app.booking import create_booking
    from app import schemas

    restaurant = db.query(models.Restaurant).filter(models.Restaurant.id == 1).first()
    assert restaurant.seating_capacity == 40
    for i in range(2):
        create_booking(db, restaurant, schemas.BookingCreate(
            customer_name=f"Filler Booking {i}", phone="07911 000000", email=f"filler{i}@example.com",
            booking_date=d, booking_time=_SAFE_TIME, party_size=20,
        ))

    process_incoming_message(phone_number_id, _text_message("wamid.booking.rejected", "447911200002", "Book me a table"))

    db.expire_all()
    bookings_after = db.query(models.Booking).filter(
        models.Booking.restaurant_id == 1, models.Booking.customer_name == "Too Many People"
    ).count()
    assert bookings_after == 0
    assert len(sent) == 1
    assert "Sorry" in sent[0][2] or "no availability" in sent[0][2].lower()
    assert db.query(models.Booking).filter(models.Booking.restaurant_id == 1).count() == bookings_before + 2


def test_whatsapp_booking_is_tenant_isolated(client, monkeypatch, db, second_restaurant, whatsapp_number):
    """A booking made through restaurant A's WhatsApp number must be
    recorded against restaurant A, never restaurant B, even though both
    restaurants may share the same underlying booking table."""
    phone_number_id_a = whatsapp_number(1, phone_number_id="4000000000000001")
    phone_number_id_b = whatsapp_number(second_restaurant, phone_number_id="4000000000000002")

    d = _fresh_date()
    args = {
        "customer_name": "Restaurant A Customer",
        "phone": "07911 555002",
        "email": "restaurant-a@example.com",
        "booking_date": d.isoformat(),
        "booking_time": _SAFE_TIME,
        "party_size": 2,
    }
    _stub_tool_round_trip(monkeypatch, args, "Booked!")
    _stub_send_ok(monkeypatch)

    process_incoming_message(phone_number_id_a, _text_message("wamid.tenant.a", "447911200003", "Book me a table"))

    booking = db.query(models.Booking).filter(models.Booking.customer_name == "Restaurant A Customer").one()
    assert booking.restaurant_id == 1
    assert booking.restaurant_id != second_restaurant


def test_duplicate_webhook_does_not_double_book(client, monkeypatch, db, whatsapp_number):
    phone_number_id = whatsapp_number(1)
    d = _fresh_date()
    args = {
        "customer_name": "Duplicate Webhook Customer",
        "phone": "07911 555003",
        "email": "dup-webhook@example.com",
        "booking_date": d.isoformat(),
        "booking_time": _SAFE_TIME,
        "party_size": 2,
    }
    _stub_tool_round_trip(monkeypatch, args, "Booked!")
    _stub_send_ok(monkeypatch)

    message = _text_message("wamid.dup.book", "447911200004", "Book me a table")
    process_incoming_message(phone_number_id, message)
    # Second stub call would raise IndexError if reached a third time
    # (only 2 responses queued), proving a genuine redelivery calls
    # Gemini/create_booking zero additional times.
    process_incoming_message(phone_number_id, dict(message))

    db.expire_all()
    count = db.query(models.Booking).filter(models.Booking.customer_name == "Duplicate Webhook Customer").count()
    assert count == 1


def test_booking_succeeds_but_send_failure_leaves_booking_and_does_not_persist_turn(
    client, monkeypatch, db, whatsapp_number
):
    """Mirrors tests/test_chat_persistence.py's
    test_booking_succeeds_but_final_reply_failure_does_not_persist_the_turn
    for the WhatsApp channel: the booking tool handler commits inside
    generate_reply(), before the outbound Send API call. If Send then
    fails, the booking is real and NOT rolled back (no compensating-
    transaction mechanism — same accepted tradeoff as web chat), but the
    turn must never be falsely persisted."""
    phone_number_id = whatsapp_number(1)
    d = _fresh_date()
    args = {
        "customer_name": "Send Failure Customer",
        "phone": "07911 555004",
        "email": "send-failure@example.com",
        "booking_date": d.isoformat(),
        "booking_time": _SAFE_TIME,
        "party_size": 2,
    }
    _stub_tool_round_trip(monkeypatch, args, "Booked!")

    def _boom(phone_number_id, to, text):
        raise whatsapp_client.WhatsAppSendError("simulated send failure")

    monkeypatch.setattr(whatsapp_client, "send_text_message", _boom)

    process_incoming_message(phone_number_id, _text_message("wamid.booksend.fail", "447911200005", "Book me a table"))

    db.expire_all()
    booking = db.query(models.Booking).filter(models.Booking.customer_name == "Send Failure Customer").one()
    assert booking.status == "confirmed"

    assert (
        db.query(models.Message).filter(models.Message.external_message_id == "wamid.booksend.fail").first()
        is None
    )


def test_whatsapp_booking_never_triggers_a_confirmation_email(client, monkeypatch, db, whatsapp_number):
    """Regression test for Booking Confirmation Email v1 (email only,
    scoped to web chat + admin-created bookings): make_booking_tool_handler
    gained an optional confirmed_booking_ids parameter that WhatsApp's
    call site (app/whatsapp_processing.py) deliberately does not pass,
    so it should be impossible for a WhatsApp-originated booking to ever
    schedule or send a confirmation email. Asserted positively here
    (the send function must never be called) rather than just relying on
    the absence of a call site, so a future accidental wiring-up would
    fail this test immediately."""
    from app import config, email_client

    monkeypatch.setattr(config, "SMTP_HOST", "smtp.example.test")
    monkeypatch.setattr(config, "EMAIL_FROM", "bookings@example.test")
    email_calls = []
    monkeypatch.setattr(email_client, "send_booking_confirmation_email", lambda **k: email_calls.append(k))

    phone_number_id = whatsapp_number(1)
    d = _fresh_date()
    args = {
        "customer_name": "No Email Expected Customer",
        "phone": "07911 555005",
        "email": "no-email-expected@example.com",
        "booking_date": d.isoformat(),
        "booking_time": _SAFE_TIME,
        "party_size": 2,
    }
    _stub_tool_round_trip(monkeypatch, args, "Booked!")
    _stub_send_ok(monkeypatch)

    process_incoming_message(phone_number_id, _text_message("wamid.no.email", "447911200006", "Book me a table"))

    db.expire_all()
    booking = db.query(models.Booking).filter(models.Booking.customer_name == "No Email Expected Customer").one()
    assert booking.status == "confirmed"
    assert booking.confirmation_sent_at is None
    assert email_calls == []
