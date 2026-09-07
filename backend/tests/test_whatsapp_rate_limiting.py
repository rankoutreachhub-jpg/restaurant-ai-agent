"""
WhatsApp-specific rate limiting (Stage 3 Step 6B): keyed by
(phone_number_id, customer phone number) via
app.rate_limit.whatsapp_rate_limiter, exercised directly through
app.whatsapp_processing.process_incoming_message rather than the HTTP
layer, since the limiter check happens inside background processing,
not as a FastAPI dependency.
"""

from types import SimpleNamespace

from app import llm as llm_module
from app import whatsapp_client
from app.whatsapp_processing import process_incoming_message
from app.rate_limit import whatsapp_rate_limiter


def _text_message(msg_id, from_number, body):
    return {"id": msg_id, "from": from_number, "type": "text", "text": {"body": body}}


def _stub_plain_reply(monkeypatch, text):
    def fake_generate_content(model, contents, config):
        return SimpleNamespace(text=text, function_calls=[])

    monkeypatch.setattr(llm_module.client.models, "generate_content", fake_generate_content)


def _stub_send_ok(monkeypatch):
    sent = []
    monkeypatch.setattr(
        whatsapp_client, "send_text_message",
        lambda phone_number_id, to, text: sent.append((phone_number_id, to, text)),
    )
    return sent


def test_customer_is_rate_limited_after_max_messages(client, monkeypatch, db, whatsapp_number):
    phone_number_id = whatsapp_number(1)
    _stub_plain_reply(monkeypatch, "reply")
    sent = _stub_send_ok(monkeypatch)

    for i in range(whatsapp_rate_limiter.max_requests):
        process_incoming_message(
            phone_number_id, _text_message(f"wamid.rl.{i}", "447911300001", f"message {i}")
        )

    assert len(sent) == whatsapp_rate_limiter.max_requests

    process_incoming_message(
        phone_number_id, _text_message("wamid.rl.over", "447911300001", "one too many")
    )
    assert len(sent) == whatsapp_rate_limiter.max_requests  # not incremented


def test_rate_limit_is_per_customer_not_global(client, monkeypatch, db, whatsapp_number):
    """A different customer phone number must have its own independent
    budget, even against the same restaurant's WhatsApp number."""
    phone_number_id = whatsapp_number(1)
    _stub_plain_reply(monkeypatch, "reply")
    sent = _stub_send_ok(monkeypatch)

    for i in range(whatsapp_rate_limiter.max_requests):
        process_incoming_message(
            phone_number_id, _text_message(f"wamid.rl2.a.{i}", "447911300002", f"message {i}")
        )
    assert len(sent) == whatsapp_rate_limiter.max_requests

    # A different customer's very first message must still go through.
    process_incoming_message(
        phone_number_id, _text_message("wamid.rl2.b.1", "447911300003", "hi from someone else")
    )
    assert len(sent) == whatsapp_rate_limiter.max_requests + 1


def test_rate_limit_is_per_phone_number_id_too(client, monkeypatch, db, whatsapp_number, second_restaurant):
    """The same customer phone messaging two DIFFERENT restaurants'
    WhatsApp numbers must have independent budgets — the key is
    (phone_number_id, customer_phone), not customer_phone alone."""
    phone_number_id_a = whatsapp_number(1, phone_number_id="5000000000000001")
    phone_number_id_b = whatsapp_number(second_restaurant, phone_number_id="5000000000000002")
    _stub_plain_reply(monkeypatch, "reply")
    sent = _stub_send_ok(monkeypatch)

    for i in range(whatsapp_rate_limiter.max_requests):
        process_incoming_message(
            phone_number_id_a, _text_message(f"wamid.rl3.a.{i}", "447911300004", f"message {i}")
        )
    assert len(sent) == whatsapp_rate_limiter.max_requests

    process_incoming_message(
        phone_number_id_b, _text_message("wamid.rl3.b.1", "447911300004", "same customer, different restaurant")
    )
    assert len(sent) == whatsapp_rate_limiter.max_requests + 1


def test_rate_limited_message_is_not_processed_or_persisted(client, monkeypatch, db, whatsapp_number):
    from app import models

    phone_number_id = whatsapp_number(1)
    call_count = {"n": 0}

    def fake_generate_content(model, contents, config):
        call_count["n"] += 1
        return SimpleNamespace(text="reply", function_calls=[])

    monkeypatch.setattr(llm_module.client.models, "generate_content", fake_generate_content)
    _stub_send_ok(monkeypatch)

    for i in range(whatsapp_rate_limiter.max_requests):
        process_incoming_message(
            phone_number_id, _text_message(f"wamid.rl4.{i}", "447911300005", f"message {i}")
        )
    calls_before_overflow = call_count["n"]

    process_incoming_message(
        phone_number_id, _text_message("wamid.rl4.over", "447911300005", "over the limit")
    )

    assert call_count["n"] == calls_before_overflow  # Gemini never called for the throttled message
    db.expire_all()
    assert db.query(models.Message).filter(models.Message.external_message_id == "wamid.rl4.over").first() is None
