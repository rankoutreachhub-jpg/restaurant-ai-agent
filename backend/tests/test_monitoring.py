"""
Production error tracking (Sentry) — Production Readiness Audit
BLOCKER #1. See app/monitoring.py for the full design rationale.

Every test here resets the global sentry_sdk client afterwards (via the
`_reset_sentry` autouse fixture) so no test's Sentry configuration ever
leaks into another test — sentry_sdk keeps its client as shared global
state, not something scoped per test the way this project's own
fixtures (db, client) are.
"""

import sentry_sdk
import pytest
from sentry_sdk.transport import Transport

from app import config, monitoring


class _FakeTransport(Transport):
    """Records captured envelopes in memory — never touches the network,
    so tests can assert on what WOULD have been sent to Sentry without
    a real DSN or any outbound HTTP call."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.envelopes = []

    def capture_envelope(self, envelope):
        self.envelopes.append(envelope)

    def captured_events(self):
        events = []
        for envelope in self.envelopes:
            for item in envelope.items:
                if item.type == "event" and item.payload:
                    events.append(item.payload.json)
        return events


@pytest.fixture(autouse=True)
def _reset_sentry():
    yield
    # Restores the pristine "nothing configured" state — confirmed
    # empirically that dsn/transport both become None again, which is
    # what actually matters (no client is ever active, nothing is ever
    # sent), even though sentry_sdk's own is_active() flag is not a
    # reliable "was this reset" signal by itself.
    sentry_sdk.init(dsn=None)


# --- Disabled when DSN is missing ---

def test_sentry_is_a_no_op_when_dsn_is_not_configured():
    assert config.SENTRY_DSN == ""  # the default in this test session — see conftest.py
    monitoring.init_sentry()
    assert sentry_sdk.get_client().dsn is None


def test_capturing_an_exception_is_harmless_when_not_configured():
    monitoring.init_sentry()
    try:
        raise ValueError("this should go nowhere")
    except ValueError:
        # Must not raise, even though nothing is configured to receive it.
        sentry_sdk.capture_exception()


# --- Captures exceptions when configured ---

def test_exception_is_captured_when_a_dsn_is_configured(monkeypatch):
    monkeypatch.setattr(config, "SENTRY_DSN", "https://fakepublickey@o0.ingest.sentry.io/1")
    transport = _FakeTransport()
    monitoring.init_sentry(transport=transport)

    assert sentry_sdk.get_client().dsn == config.SENTRY_DSN

    try:
        raise ValueError("a real captured error")
    except ValueError:
        sentry_sdk.capture_exception()

    events = transport.captured_events()
    assert len(events) == 1
    assert events[0]["exception"]["values"][0]["type"] == "ValueError"


# --- Sensitive data is never sent ---

def test_before_send_strips_request_headers_body_and_query_string():
    event = {
        "request": {
            "url": "https://api.example.com/admin/restaurant/1?foo=bar",
            "method": "GET",
            "headers": {
                "X-Admin-API-Key": "super-secret-admin-key",
                "X-Hub-Signature-256": "sha256=abc123",
                "X-Conversation-Token": "opaque-customer-token",
            },
            "data": {"message": "the customer's actual chat message"},
            "cookies": {"session": "abc"},
            "query_string": "foo=bar",
        },
        "exception": {"values": [{"type": "ValueError", "value": "boom"}]},
    }

    result = monitoring.before_send(event, {})

    assert "headers" not in result["request"]
    assert "data" not in result["request"]
    assert "cookies" not in result["request"]
    assert "query_string" not in result["request"]
    assert "?" not in result["request"]["url"] or "foo=bar" not in result["request"]["url"]
    assert "super-secret-admin-key" not in str(result)
    assert "opaque-customer-token" not in str(result)
    assert "the customer's actual chat message" not in str(result)


def test_before_send_redacts_the_known_pii_bearing_exception_message():
    event = {
        "exception": {
            "values": [
                {"type": "InvalidPhoneNumberError", "value": "Invalid phone number: '+447700900123'"}
            ]
        }
    }

    result = monitoring.before_send(event, {})

    assert "447700900123" not in str(result)
    assert result["exception"]["values"][0]["type"] == "InvalidPhoneNumberError"  # type still visible


def test_before_send_never_raises_on_an_unexpected_event_shape():
    assert monitoring.before_send({"request": "not-a-dict"}, {}) is not None
    assert monitoring.before_send({}, {}) == {}


def test_before_breadcrumb_strips_query_string_from_outgoing_call_urls():
    breadcrumb = {
        "category": "httplib",
        "data": {"url": "https://generativelanguage.googleapis.com/v1/models?key=AIzaSyREALKEY"},
    }

    result = monitoring.before_breadcrumb(breadcrumb, {})

    assert "AIzaSyREALKEY" not in str(result)


def test_before_breadcrumb_never_raises_on_an_unexpected_shape():
    assert monitoring.before_breadcrumb({"data": "not-a-dict"}, {}) is not None


def test_local_variables_are_never_attached_when_configured(monkeypatch):
    """
    The single most important safety property: a real customer phone
    number/message sitting in a local variable at the point an
    exception is raised must never be attached to the captured event —
    this is what include_local_variables=False in init_sentry()
    guarantees.

    The sensitive values are assembled at runtime from split fragments
    (never as one contiguous literal anywhere in this file's source) —
    Sentry separately captures a few lines of surrounding SOURCE CODE
    per stack frame (an entirely different feature from local-variable
    capture, not governed by include_local_variables at all), which
    would make a literal string sitting in this test's own source show
    up in the event regardless of that setting — a testing artifact
    that says nothing about the real app, where these values are
    always runtime data from a request/database row, never a source
    literal anywhere in the codebase.
    """
    monkeypatch.setattr(config, "SENTRY_DSN", "https://fakepublickey@o0.ingest.sentry.io/1")
    transport = _FakeTransport()
    monitoring.init_sentry(transport=transport)

    def _raise_with_sensitive_locals(customer_phone, customer_message):
        raise RuntimeError("something went wrong")

    # Built by reversing a reversed literal — never appears anywhere in
    # this file's source as the actual, contiguous, matchable value, so
    # a source-context capture of these lines cannot produce a false
    # positive the way even a split "a" + "b" concatenation still can
    # (a literal reading nearby lines, not the concatenated result).
    phone = "3210090077440+"[::-1]
    message = ("rebmun taht no em llac ,moc.elpmaxe@enaj si liame ym")[::-1]
    try:
        _raise_with_sensitive_locals(phone, message)
    except RuntimeError:
        sentry_sdk.capture_exception()

    events = transport.captured_events()
    assert len(events) == 1
    event_text = str(events[0])
    assert phone not in event_text
    assert "jane@example.com" not in event_text


# --- Existing behavior preserved ---

def test_health_endpoint_is_unaffected_by_sentry_configuration(client, monkeypatch):
    monkeypatch.setattr(config, "SENTRY_DSN", "https://fakepublickey@o0.ingest.sentry.io/1")
    monitoring.init_sentry(transport=_FakeTransport())

    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_generic_500_response_is_unchanged_whether_or_not_sentry_is_configured(
    client, admin_headers, monkeypatch
):
    from app import llm as llm_module

    def fake_generate_content(model, contents, config):
        raise RuntimeError("simulated Gemini failure")

    monkeypatch.setattr(llm_module.client.models, "generate_content", fake_generate_content)

    # Without Sentry configured.
    response_without = client.post("/chat", json={"message": "hi", "history": [], "restaurant_id": 1})
    assert response_without.status_code == 500
    body_without = response_without.json()
    assert body_without == {"detail": "Sorry, something went wrong on our end. Please try again shortly."}

    # With Sentry configured — the response contract must not change.
    monkeypatch.setattr(config, "SENTRY_DSN", "https://fakepublickey@o0.ingest.sentry.io/1")
    monitoring.init_sentry(transport=_FakeTransport())

    response_with = client.post("/chat", json={"message": "hi", "history": [], "restaurant_id": 1})
    assert response_with.status_code == 500
    assert response_with.json() == body_without
    assert "RuntimeError" not in str(response_with.json())
    assert "simulated Gemini failure" not in str(response_with.json())
