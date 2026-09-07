"""
GET/POST /webhooks/whatsapp: Meta's verification handshake and the
HMAC-SHA256 signature check on every webhook POST (Stage 3 Step 6B).

Signature is computed over the RAW request body — these tests build the
body as raw bytes and send it with content=..., never json=..., so what
gets signed and what gets sent are guaranteed to be byte-identical (the
exact property app/routers/whatsapp.py's docstring calls out as
important: a re-serialized JSON object could differ byte-for-byte from
what was actually signed).
"""

import hashlib
import hmac
import json

from app import config

WEBHOOK_URL = "/webhooks/whatsapp"


def _sign(body: bytes) -> str:
    digest = hmac.new(config.WHATSAPP_APP_SECRET.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def _empty_payload_body() -> bytes:
    return json.dumps({"object": "whatsapp_business_account", "entry": []}).encode("utf-8")


# --- GET verification ---

def test_get_verification_succeeds_with_correct_token(client):
    response = client.get(
        WEBHOOK_URL,
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": config.WHATSAPP_VERIFY_TOKEN,
            "hub.challenge": "12345",
        },
    )
    assert response.status_code == 200
    assert response.text == "12345"


def test_get_verification_fails_with_wrong_token(client):
    response = client.get(
        WEBHOOK_URL,
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": "definitely-the-wrong-token",
            "hub.challenge": "12345",
        },
    )
    assert response.status_code == 403


def test_get_verification_fails_with_wrong_mode(client):
    response = client.get(
        WEBHOOK_URL,
        params={
            "hub.mode": "unsubscribe",
            "hub.verify_token": config.WHATSAPP_VERIFY_TOKEN,
            "hub.challenge": "12345",
        },
    )
    assert response.status_code == 403


def test_get_verification_fails_when_token_not_configured(client, monkeypatch):
    monkeypatch.setattr(config, "WHATSAPP_VERIFY_TOKEN", "")
    response = client.get(
        WEBHOOK_URL,
        params={"hub.mode": "subscribe", "hub.verify_token": "", "hub.challenge": "12345"},
    )
    assert response.status_code == 403


# --- POST signature verification ---

def test_post_with_valid_signature_is_accepted(client):
    body = _empty_payload_body()
    response = client.post(
        WEBHOOK_URL, content=body, headers={"X-Hub-Signature-256": _sign(body)}
    )
    assert response.status_code == 200


def test_post_with_invalid_signature_is_rejected(client):
    body = _empty_payload_body()
    response = client.post(
        WEBHOOK_URL,
        content=body,
        headers={"X-Hub-Signature-256": "sha256=0000000000000000000000000000000000000000000000000000000000000000"},
    )
    assert response.status_code == 401


def test_post_with_missing_signature_is_rejected(client):
    body = _empty_payload_body()
    response = client.post(WEBHOOK_URL, content=body)
    assert response.status_code == 401


def test_post_signature_for_a_different_body_is_rejected(client):
    """Proves the signature is checked against the RAW body actually
    sent, not some other representation of it — a signature computed
    over a different (even very similar) payload must not validate."""
    real_body = _empty_payload_body()
    signature_for_different_body = _sign(json.dumps({"object": "whatsapp_business_account", "entry": [1]}).encode("utf-8"))
    response = client.post(
        WEBHOOK_URL, content=real_body, headers={"X-Hub-Signature-256": signature_for_different_body}
    )
    assert response.status_code == 401


def test_post_is_rejected_when_app_secret_not_configured(client, monkeypatch):
    monkeypatch.setattr(config, "WHATSAPP_APP_SECRET", "")
    body = _empty_payload_body()
    response = client.post(
        WEBHOOK_URL, content=body, headers={"X-Hub-Signature-256": _sign(body)}
    )
    assert response.status_code == 401


def test_post_with_invalid_json_after_valid_signature_is_rejected(client):
    body = b"this is not json"
    response = client.post(
        WEBHOOK_URL, content=body, headers={"X-Hub-Signature-256": _sign(body)}
    )
    assert response.status_code == 400


def test_post_rejects_before_scheduling_any_processing(client, monkeypatch):
    """Signature verification must happen before any message processing
    is even scheduled — proven by asserting process_incoming_message
    (which does all DB access; see app/whatsapp_processing.py) is never
    invoked for an invalid-signature request, even one whose body
    contains a real-looking message."""
    from app.routers import whatsapp as whatsapp_router

    calls = []
    monkeypatch.setattr(whatsapp_router, "process_incoming_message", lambda *a, **kw: calls.append((a, kw)))

    body = json.dumps({
        "object": "whatsapp_business_account",
        "entry": [{"changes": [{"value": {
            "metadata": {"phone_number_id": "123"},
            "messages": [{"id": "wamid.1", "from": "447911123456", "type": "text", "text": {"body": "hi"}}],
        }}]}],
    }).encode("utf-8")

    response = client.post(
        WEBHOOK_URL,
        content=body,
        headers={"X-Hub-Signature-256": "sha256=" + "0" * 64},
    )
    assert response.status_code == 401
    assert calls == []
