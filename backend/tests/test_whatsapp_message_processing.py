"""
app/whatsapp_processing.process_incoming_message (Stage 3 Step 6B):
message idempotency, duplicate webhook delivery, Gemini failure, Send
API failure, and the "never log the raw payload / secrets / content"
logging policy.
"""

import hashlib
import hmac
import json
from types import SimpleNamespace

from app import config, models
from app import llm as llm_module
from app import whatsapp_client
from app.whatsapp_processing import process_incoming_message

WEBHOOK_URL = "/webhooks/whatsapp"


def _sign(body: bytes) -> str:
    digest = hmac.new(config.WHATSAPP_APP_SECRET.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


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


def _stub_send_fails(monkeypatch):
    def _boom(phone_number_id, to, text):
        raise whatsapp_client.WhatsAppSendError("simulated send failure")

    monkeypatch.setattr(whatsapp_client, "send_text_message", _boom)


def _messages_for(db, external_message_id):
    return (
        db.query(models.Message)
        .filter(models.Message.external_message_id == external_message_id)
        .all()
    )


def test_message_is_persisted_with_its_external_message_id(client, monkeypatch, db, whatsapp_number):
    phone_number_id = whatsapp_number(1)
    _stub_plain_reply(monkeypatch, "Hiya!")
    _stub_send_ok(monkeypatch)

    process_incoming_message(phone_number_id, _text_message("wamid.idem.1", "447911100001", "hi"))

    db.expire_all()
    rows = _messages_for(db, "wamid.idem.1")
    assert len(rows) == 1
    assert rows[0].role == "user"


def test_duplicate_message_id_is_not_processed_twice(client, monkeypatch, db, whatsapp_number):
    """The core idempotency guarantee: redelivering the exact same
    message id must not call Gemini again, must not persist a second
    time, and must not send a second outbound reply."""
    phone_number_id = whatsapp_number(1)
    call_count = {"n": 0}

    def fake_generate_content(model, contents, config):
        call_count["n"] += 1
        return SimpleNamespace(text="Hiya!", function_calls=[])

    monkeypatch.setattr(llm_module.client.models, "generate_content", fake_generate_content)
    sent = _stub_send_ok(monkeypatch)

    message = _text_message("wamid.idem.2", "447911100002", "hi")
    process_incoming_message(phone_number_id, message)
    process_incoming_message(phone_number_id, dict(message))  # exact redelivery

    db.expire_all()
    assert call_count["n"] == 1
    assert len(sent) == 1
    assert len(_messages_for(db, "wamid.idem.2")) == 1


def test_duplicate_full_webhook_post_does_not_double_process(client, monkeypatch):
    """End-to-end: Meta redelivering the entire webhook HTTP POST (not
    just calling the pipeline function twice) must be equally safe."""
    from app.routers import platform_admin  # noqa: F401  (ensures app wired)

    call_count = {"n": 0}

    def fake_generate_content(model, contents, config):
        call_count["n"] += 1
        return SimpleNamespace(text="Hiya!", function_calls=[])

    monkeypatch.setattr(llm_module.client.models, "generate_content", fake_generate_content)
    _stub_send_ok(monkeypatch)

    body = json.dumps({
        "object": "whatsapp_business_account",
        "entry": [{"changes": [{"value": {
            "metadata": {"phone_number_id": "3000000000000000"},
            "messages": [{"id": "wamid.dup.post", "from": "447911100003", "type": "text", "text": {"body": "hi"}}],
        }}]}],
    }).encode("utf-8")
    headers = {"X-Hub-Signature-256": _sign(body)}

    # Map the restaurant via the real admin endpoint first.
    client.post(
        "/admin/platform/restaurants/1/whatsapp-number",
        json={"phone_number_id": "3000000000000000", "display_phone_number": "+15551234567"},
        headers={"X-Admin-API-Key": config.ADMIN_API_KEY},
    )

    first = client.post(WEBHOOK_URL, content=body, headers=headers)
    second = client.post(WEBHOOK_URL, content=body, headers=headers)

    assert first.status_code == 200
    assert second.status_code == 200
    assert call_count["n"] == 1


def test_gemini_failure_does_not_send_or_persist(client, monkeypatch, db, whatsapp_number):
    phone_number_id = whatsapp_number(1)

    def fake_generate_content(model, contents, config):
        raise RuntimeError("simulated Gemini failure")

    monkeypatch.setattr(llm_module.client.models, "generate_content", fake_generate_content)
    sent = _stub_send_ok(monkeypatch)

    process_incoming_message(phone_number_id, _text_message("wamid.gemini.fail", "447911100004", "hi"))

    db.expire_all()
    assert sent == []
    assert _messages_for(db, "wamid.gemini.fail") == []


def test_send_api_failure_does_not_persist_the_turn(client, monkeypatch, db, whatsapp_number):
    phone_number_id = whatsapp_number(1)
    _stub_plain_reply(monkeypatch, "Hiya!")
    _stub_send_fails(monkeypatch)

    process_incoming_message(phone_number_id, _text_message("wamid.send.fail", "447911100005", "hi"))

    db.expire_all()
    assert _messages_for(db, "wamid.send.fail") == []


def _whatsapp_conversations_for(db, customer_phone_e164):
    return (
        db.query(models.Conversation)
        .filter(
            models.Conversation.channel == "whatsapp",
            models.Conversation.external_id == customer_phone_e164,
        )
        .all()
    )


def test_gemini_failure_creates_no_orphaned_conversation(client, monkeypatch, db, whatsapp_number):
    """
    Regression test: a brand-new WhatsApp conversation whose first
    Gemini call fails must leave no orphaned Conversation row --
    complementing test_gemini_failure_does_not_send_or_persist's
    message-level check with the actual production symptom (a
    conversation visible in admin with zero persisted messages).
    Root cause was get_or_create_whatsapp_conversation() committing the
    new conversation immediately, before this call could fail.
    """
    phone_number_id = whatsapp_number(1)

    def fake_generate_content(model, contents, config):
        raise RuntimeError("simulated Gemini failure")

    monkeypatch.setattr(llm_module.client.models, "generate_content", fake_generate_content)
    _stub_send_ok(monkeypatch)

    process_incoming_message(phone_number_id, _text_message("wamid.gemini.fail.orphan", "447911100099", "hi"))

    db.expire_all()
    assert _whatsapp_conversations_for(db, "+447911100099") == []


def test_unsupported_message_type_is_dropped_without_error(client, monkeypatch, db, whatsapp_number):
    phone_number_id = whatsapp_number(1)
    call_count = {"n": 0}
    monkeypatch.setattr(
        llm_module.client.models, "generate_content",
        lambda model, contents, config: call_count.__setitem__("n", call_count["n"] + 1) or SimpleNamespace(text="x", function_calls=[]),
    )
    sent = _stub_send_ok(monkeypatch)

    process_incoming_message(phone_number_id, {"id": "wamid.media", "from": "447911100006", "type": "image", "image": {"id": "abc"}})

    assert call_count["n"] == 0
    assert sent == []


def test_missing_message_id_is_dropped_without_error(client, monkeypatch, db, whatsapp_number):
    phone_number_id = whatsapp_number(1)
    sent = _stub_send_ok(monkeypatch)

    process_incoming_message(phone_number_id, {"from": "447911100007", "type": "text", "text": {"body": "hi"}})

    assert sent == []


def test_invalid_sender_phone_number_is_dropped_without_error(client, monkeypatch, db, whatsapp_number):
    phone_number_id = whatsapp_number(1)
    sent = _stub_send_ok(monkeypatch)

    process_incoming_message(phone_number_id, _text_message("wamid.badphone", "not-a-real-number", "hi"))

    assert sent == []


def test_no_raw_webhook_payload_or_message_content_or_secrets_in_logs(client, monkeypatch, db, whatsapp_number, caplog):
    import logging

    phone_number_id = whatsapp_number(1)
    _stub_plain_reply(monkeypatch, "a very specific assistant reply about jackfruit tacos")
    _stub_send_ok(monkeypatch)

    secret_customer_message = "my secret message content mentioning jackfruit tacos"
    customer_phone = "447911100008"

    with caplog.at_level(logging.DEBUG):
        process_incoming_message(
            phone_number_id, _text_message("wamid.logcheck", customer_phone, secret_customer_message)
        )

    log_text = "\n".join(record.getMessage() for record in caplog.records)
    assert secret_customer_message not in log_text
    assert "jackfruit tacos" not in log_text
    assert customer_phone not in log_text
    assert config.WHATSAPP_ACCESS_TOKEN not in log_text
    assert config.WHATSAPP_APP_SECRET not in log_text
    assert config.WHATSAPP_VERIFY_TOKEN not in log_text
