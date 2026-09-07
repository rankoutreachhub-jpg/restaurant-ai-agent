"""
phone_number_id -> restaurant resolution (Stage 3 Step 6B). Restaurant
identity is NEVER taken from the message payload itself — only from a
WhatsAppNumber row looked up by phone_number_id. An unrecognised
phone_number_id must drop the message, never default to any restaurant.
"""

from types import SimpleNamespace

from app import models
from app import llm as llm_module
from app import whatsapp_client
from app.whatsapp_processing import process_incoming_message

_KNOWN_PHONE_NUMBER_ID = "1000000000000000"
_UNKNOWN_PHONE_NUMBER_ID = "9999999999999999"


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


def _whatsapp_conversations_for(db, restaurant_id, external_id):
    """Scoped to (restaurant_id, channel='whatsapp', external_id) rather
    than restaurant_id alone — restaurant 1 in particular accumulates
    web-channel conversations from every other test file across the
    shared test-session database (see conftest.py), so this must never
    assume it's the only conversation that restaurant has."""
    return (
        db.query(models.Conversation)
        .filter(
            models.Conversation.restaurant_id == restaurant_id,
            models.Conversation.channel == "whatsapp",
            models.Conversation.external_id == external_id,
        )
        .all()
    )


def test_unknown_phone_number_id_drops_the_message(client, monkeypatch, db):
    _stub_plain_reply(monkeypatch, "should never be reached")
    sent = _stub_send_ok(monkeypatch)
    messages_before = db.query(models.Message).count()
    conversations_before = db.query(models.Conversation).count()

    process_incoming_message(_UNKNOWN_PHONE_NUMBER_ID, _text_message("wamid.1", "447911123456", "hi"))

    assert sent == []
    db.expire_all()
    assert db.query(models.Message).count() == messages_before
    assert db.query(models.Conversation).count() == conversations_before


def test_known_phone_number_id_resolves_to_the_mapped_restaurant(client, monkeypatch, db, whatsapp_number):
    phone_number_id = whatsapp_number(1)
    _stub_plain_reply(monkeypatch, "Hiya!")
    _stub_send_ok(monkeypatch)

    process_incoming_message(phone_number_id, _text_message("wamid.2", "447911100100", "hi"))

    db.expire_all()
    conversations = _whatsapp_conversations_for(db, restaurant_id=1, external_id="+447911100100")
    assert len(conversations) == 1
    assert conversations[0].channel == "whatsapp"
    assert conversations[0].external_id == "+447911100100"


def test_cross_tenant_isolation_between_two_whatsapp_numbers(client, monkeypatch, db, second_restaurant, whatsapp_number):
    phone_number_id_1 = whatsapp_number(1, phone_number_id="1111111111111111")
    phone_number_id_2 = whatsapp_number(second_restaurant, phone_number_id="2222222222222222")

    captured_context = []

    def fake_generate_content(model, contents, config):
        captured_context.append(config.system_instruction)
        return SimpleNamespace(text="reply", function_calls=[])

    monkeypatch.setattr(llm_module.client.models, "generate_content", fake_generate_content)
    _stub_send_ok(monkeypatch)

    # Same customer phone number messaging BOTH restaurants' WhatsApp numbers.
    customer_phone = "447911100200"
    process_incoming_message(phone_number_id_1, _text_message("wamid.r1", customer_phone, "hi restaurant 1"))
    process_incoming_message(phone_number_id_2, _text_message("wamid.r2", customer_phone, "hi restaurant 2"))

    db.expire_all()
    conv1 = _whatsapp_conversations_for(db, restaurant_id=1, external_id="+" + customer_phone)
    conv2 = _whatsapp_conversations_for(db, restaurant_id=second_restaurant, external_id="+" + customer_phone)
    assert len(conv1) == 1
    assert len(conv2) == 1
    assert conv1[0].id != conv2[0].id

    # Restaurant 1's system context never contains restaurant 2's data and
    # vice versa (proves knowledge.build_restaurant_context was built for
    # the RESOLVED restaurant, not guessed/shared).
    assert "The Anchor" not in captured_context[0]
    assert "The Kings Arms" not in captured_context[1]


def test_unmapped_phone_number_id_never_falls_back_to_another_restaurant(client, monkeypatch, db, whatsapp_number):
    """Restaurant 1 has a mapped number; the unknown id used here must
    NOT resolve to restaurant 1 (or any restaurant) just because one
    exists."""
    whatsapp_number(1, phone_number_id="1111111111111111")
    _stub_plain_reply(monkeypatch, "should never be reached")
    sent = _stub_send_ok(monkeypatch)
    conversations_before = db.query(models.Conversation).count()

    process_incoming_message("0000000000000000", _text_message("wamid.3", "447911123456", "hi"))

    assert sent == []
    db.expire_all()
    assert db.query(models.Conversation).count() == conversations_before
