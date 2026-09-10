"""
Booking confirmation email (Production Readiness: notifications, v1) —
web chat and admin-created bookings only, never WhatsApp (see the added
regression test in tests/test_whatsapp_booking.py).

Most tests here stub app/email_client.py's send function directly (the
same pattern tests/test_whatsapp_booking.py uses for whatsapp_client's
send function) rather than exercising a real Resend API call — the one
exception is
test_email_client_send_calls_resend_api_and_does_not_log_pii_or_credentials,
which stubs httpx.post itself to verify the real send path end to end.

TestClient runs FastAPI's BackgroundTasks synchronously before
client.post(...) returns, so DB state can be asserted immediately
afterwards without any sleep/poll.

Date allocation: see tests/test_booking_service.py's module docstring
for the overall convention. This file's anchor (+200) sits between
tests/test_booking_admin.py's (+150) and tests/test_chat_booking_tool.py's
(+250) — a 50-day gap from each, not a multiple of 7, so the three can
never share a date.
"""

import itertools
from datetime import date, timedelta
from types import SimpleNamespace

import httpx

from app import config, email_client, models
from app import llm as llm_module
from app.booking_notifications import send_confirmation_email_task

_ANCHOR = date.today() + timedelta(days=200)
_counter = itertools.count(1)

_SAFE_TIME = "13:00"


def _fresh_date() -> date:
    return _ANCHOR + timedelta(weeks=next(_counter))


def _booking_payload(**overrides):
    payload = {
        "customer_name": "Jane Doe",
        "phone": "01234 567890",
        "email": "jane@example.com",
        "booking_date": _fresh_date().isoformat(),
        "booking_time": _SAFE_TIME,
        "party_size": 4,
    }
    payload.update(overrides)
    return payload


def _configure_email(monkeypatch):
    monkeypatch.setattr(config, "RESEND_API_KEY", "re_test_0000000000000000000000")
    monkeypatch.setattr(config, "EMAIL_FROM", "bookings@example.test")


def _stub_send_ok(monkeypatch):
    calls = []

    def fake_send(**kwargs):
        calls.append(kwargs)

    monkeypatch.setattr(email_client, "send_booking_confirmation_email", fake_send)
    return calls


def _stub_send_fails(monkeypatch, exc=None):
    def fake_send(**kwargs):
        raise exc or email_client.EmailSendError("simulated SMTP failure")

    monkeypatch.setattr(email_client, "send_booking_confirmation_email", fake_send)


# =========================================================
# 1. Successful booking + email
# =========================================================

def test_admin_created_booking_sends_confirmation_email(client, monkeypatch, db, admin_headers):
    _configure_email(monkeypatch)
    calls = _stub_send_ok(monkeypatch)

    response = client.post(
        "/admin/restaurant/1/bookings", json=_booking_payload(), headers=admin_headers
    )

    assert response.status_code == 201
    booking_id = response.json()["id"]
    assert len(calls) == 1
    assert calls[0]["booking_id"] == booking_id
    assert calls[0]["to_email"] == "jane@example.com"
    assert calls[0]["party_size"] == 4

    db.expire_all()
    booking = db.query(models.Booking).filter(models.Booking.id == booking_id).one()
    assert booking.confirmation_sent_at is not None


def test_web_chat_booking_sends_confirmation_email(client, monkeypatch, db):
    _configure_email(monkeypatch)
    calls = _stub_send_ok(monkeypatch)

    d = _fresh_date()
    args = {
        "customer_name": "Chat Customer",
        "phone": "01234 999999",
        "email": "chat-customer@example.com",
        "booking_date": d.isoformat(),
        "booking_time": _SAFE_TIME,
        "party_size": 3,
    }

    def fake_response(text=None, function_calls=None):
        return SimpleNamespace(
            text=text,
            function_calls=function_calls or [],
            candidates=[SimpleNamespace(content=SimpleNamespace(role="model", parts=[]))],
        )

    call_count = {"n": 0}

    def fake_generate_content(model, contents, config):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return fake_response(function_calls=[SimpleNamespace(name="create_booking", args=args)])
        return fake_response(text="You're booked in!")

    monkeypatch.setattr(llm_module.client.models, "generate_content", fake_generate_content)

    response = client.post(
        "/chat", json={"message": "Book me a table", "history": [], "restaurant_id": 1}
    )

    assert response.status_code == 200
    assert len(calls) == 1
    assert calls[0]["to_email"] == "chat-customer@example.com"

    booking = (
        db.query(models.Booking)
        .filter(models.Booking.restaurant_id == 1, models.Booking.booking_date == d)
        .one()
    )
    assert booking.confirmation_sent_at is not None


# =========================================================
# 2. Email failure does not fail (or rollback) the booking
# =========================================================

def test_email_send_failure_does_not_fail_or_rollback_the_booking(client, monkeypatch, db, admin_headers):
    _configure_email(monkeypatch)
    _stub_send_fails(monkeypatch)

    response = client.post(
        "/admin/restaurant/1/bookings", json=_booking_payload(), headers=admin_headers
    )

    assert response.status_code == 201
    booking_id = response.json()["id"]

    db.expire_all()
    booking = db.query(models.Booking).filter(models.Booking.id == booking_id).one()
    assert booking.status == "confirmed"
    assert booking.confirmation_sent_at is None


def test_unexpected_exception_in_notification_task_does_not_propagate(client, monkeypatch, db, admin_headers):
    """Defence in depth beyond EmailSendError: even a totally unexpected
    exception inside the background task must never surface to the
    client or leave the booking in a bad state."""
    _configure_email(monkeypatch)
    _stub_send_fails(monkeypatch, exc=RuntimeError("something unrelated broke"))

    response = client.post(
        "/admin/restaurant/1/bookings", json=_booking_payload(), headers=admin_headers
    )

    assert response.status_code == 201
    booking_id = response.json()["id"]

    db.expire_all()
    booking = db.query(models.Booking).filter(models.Booking.id == booking_id).one()
    assert booking.status == "confirmed"
    assert booking.confirmation_sent_at is None


# =========================================================
# 3. Missing email configuration
# =========================================================

def test_booking_succeeds_when_email_not_configured(client, monkeypatch, db, admin_headers):
    # Deliberately NOT calling _configure_email — the default test env
    # (see conftest.py) never sets RESEND_API_KEY/EMAIL_FROM either.
    def post_should_never_be_called(*args, **kwargs):
        raise AssertionError("httpx.post must never be called when email is not configured")

    monkeypatch.setattr(email_client.httpx, "post", post_should_never_be_called)

    response = client.post(
        "/admin/restaurant/1/bookings", json=_booking_payload(), headers=admin_headers
    )

    assert response.status_code == 201
    booking_id = response.json()["id"]

    db.expire_all()
    booking = db.query(models.Booking).filter(models.Booking.id == booking_id).one()
    assert booking.status == "confirmed"
    assert booking.confirmation_sent_at is None


def test_is_email_configured_reflects_both_required_settings(monkeypatch):
    monkeypatch.setattr(config, "RESEND_API_KEY", "")
    monkeypatch.setattr(config, "EMAIL_FROM", "")
    assert email_client.is_email_configured() is False

    monkeypatch.setattr(config, "RESEND_API_KEY", "re_test_0000000000000000000000")
    monkeypatch.setattr(config, "EMAIL_FROM", "")
    assert email_client.is_email_configured() is False

    monkeypatch.setattr(config, "RESEND_API_KEY", "")
    monkeypatch.setattr(config, "EMAIL_FROM", "bookings@example.test")
    assert email_client.is_email_configured() is False

    monkeypatch.setattr(config, "RESEND_API_KEY", "re_test_0000000000000000000000")
    monkeypatch.setattr(config, "EMAIL_FROM", "bookings@example.test")
    assert email_client.is_email_configured() is True


# =========================================================
# 4. Duplicate/unintended confirmation prevention
# =========================================================

def test_confirmation_email_is_not_sent_twice_for_the_same_booking(client, monkeypatch, db, admin_headers):
    _configure_email(monkeypatch)
    calls = _stub_send_ok(monkeypatch)

    response = client.post(
        "/admin/restaurant/1/bookings", json=_booking_payload(), headers=admin_headers
    )
    booking_id = response.json()["id"]
    assert len(calls) == 1

    # Directly invoke the task again, simulating a hypothetical duplicate
    # scheduling — the idempotency guard (confirmation_sent_at already
    # set) must prevent a second send.
    send_confirmation_email_task(booking_id)
    send_confirmation_email_task(booking_id)

    assert len(calls) == 1


def test_confirmation_email_skipped_for_a_non_confirmed_booking(client, monkeypatch, db, admin_headers):
    """A booking that isn't (or is no longer) in "confirmed" status by
    the time the task runs must not get a confirmation email."""
    _configure_email(monkeypatch)
    calls = _stub_send_ok(monkeypatch)

    response = client.post(
        "/admin/restaurant/1/bookings", json=_booking_payload(), headers=admin_headers
    )
    booking_id = response.json()["id"]
    calls.clear()  # ignore the send already triggered by creation itself

    db.expire_all()
    booking = db.query(models.Booking).filter(models.Booking.id == booking_id).one()
    booking.status = "cancelled"
    booking.confirmation_sent_at = None
    db.commit()

    send_confirmation_email_task(booking_id)

    assert calls == []


def test_confirmation_email_skipped_for_an_unknown_booking_id(db, monkeypatch):
    _configure_email(monkeypatch)
    calls = _stub_send_ok(monkeypatch)

    send_confirmation_email_task(999999999)

    assert calls == []


# =========================================================
# Logging discipline (explicit hard requirement, not just F in the plan)
# =========================================================

def test_confirmation_email_flow_does_not_log_pii(client, monkeypatch, admin_headers):
    from app.logging_config import LOG_FILE

    _configure_email(monkeypatch)
    _stub_send_ok(monkeypatch)

    client.post(
        "/admin/restaurant/1/bookings",
        json=_booking_payload(
            customer_name="Log Test Person", email="logtest@example.com", phone="01234 000111"
        ),
        headers=admin_headers,
    )

    content = LOG_FILE.read_text(encoding="utf-8")
    assert "Log Test Person" not in content
    assert "logtest@example.com" not in content
    assert "01234 000111" not in content


def _fake_post_ok(monkeypatch):
    """Records what would have been sent to Resend, without any real
    network activity — returns a genuine httpx.Response(200, ...) so
    response.raise_for_status() inside email_client.py behaves exactly
    as it would for a real successful Resend call."""
    calls = []

    def fake_post(url, *, headers, json, timeout):
        calls.append({"url": url, "headers": headers, "json": json, "timeout": timeout})
        return httpx.Response(200, request=httpx.Request("POST", url))

    monkeypatch.setattr(email_client.httpx, "post", fake_post)
    return calls


def _fake_post_fails(monkeypatch, status_code=422):
    def fake_post(url, *, headers, json, timeout):
        return httpx.Response(status_code, request=httpx.Request("POST", url))

    monkeypatch.setattr(email_client.httpx, "post", fake_post)


def test_email_client_send_calls_resend_api_and_does_not_log_pii_or_credentials(monkeypatch):
    from app.logging_config import LOG_FILE

    _configure_email(monkeypatch)
    monkeypatch.setattr(config, "RESEND_API_KEY", "re_super_secret_test_key_0000000")
    calls = _fake_post_ok(monkeypatch)

    email_client.send_booking_confirmation_email(
        to_email="realsend-test@example.com",
        restaurant_name="The Kings Arms",
        booking_date="2027-01-01",
        booking_time="13:00",
        party_size=2,
        booking_id=999999,
    )

    assert len(calls) == 1
    call = calls[0]
    assert call["url"] == email_client.RESEND_API_URL
    assert call["headers"]["Authorization"] == "Bearer re_super_secret_test_key_0000000"
    assert call["json"]["to"] == ["realsend-test@example.com"]
    assert call["json"]["from"] == "bookings@example.test"
    assert "The Kings Arms" in call["json"]["subject"]
    assert "999999" in call["json"]["text"]

    content = LOG_FILE.read_text(encoding="utf-8")
    assert "realsend-test@example.com" not in content
    assert "re_super_secret_test_key_0000000" not in content


def test_email_client_raises_on_non_2xx_resend_response(monkeypatch):
    from app.logging_config import LOG_FILE

    _configure_email(monkeypatch)
    _fake_post_fails(monkeypatch, status_code=422)

    try:
        email_client.send_booking_confirmation_email(
            to_email="rejected-by-resend@example.com",
            restaurant_name="The Kings Arms",
            booking_date="2027-01-01",
            booking_time="13:00",
            party_size=2,
            booking_id=999997,
        )
        assert False, "expected EmailSendError"
    except email_client.EmailSendError:
        pass

    content = LOG_FILE.read_text(encoding="utf-8")
    assert "rejected-by-resend@example.com" not in content


def test_email_client_raises_when_not_configured(monkeypatch):
    monkeypatch.setattr(config, "RESEND_API_KEY", "")
    monkeypatch.setattr(config, "EMAIL_FROM", "")

    try:
        email_client.send_booking_confirmation_email(
            to_email="whoever@example.com",
            restaurant_name="The Kings Arms",
            booking_date="2027-01-01",
            booking_time="13:00",
            party_size=2,
            booking_id=1,
        )
        assert False, "expected EmailSendError"
    except email_client.EmailSendError:
        pass
