"""
WhatsApp webhook endpoints (Stage 3 Step 6B — Meta Cloud API).

Two routes, both required by Meta's webhook contract:
  - GET  /webhooks/whatsapp: one-time verification handshake when the
    webhook URL is configured in the Meta App Dashboard.
  - POST /webhooks/whatsapp: every actual event (incoming messages,
    delivery statuses, etc.) Meta delivers afterwards.

Security model for POST:
  1. The raw request body's HMAC-SHA256 (keyed with WHATSAPP_APP_SECRET)
     must match the X-Hub-Signature-256 header EXACTLY, compared in
     constant time, computed over the RAW bytes (not a re-serialized
     JSON object, which could differ byte-for-byte from what Meta
     signed) — checked BEFORE any JSON parsing or database access.
  2. Only after that succeeds does anything below even look at the
     payload's contents, and even then, restaurant identity is NEVER
     taken from the payload directly — only phone_number_id, looked up
     against WhatsAppNumber (app/whatsapp_processing.py). An unrecognised
     phone_number_id drops that message; it never falls back to any
     restaurant.

Meta expects a prompt HTTP 200 or it will retry (and eventually give up
and mark the webhook unhealthy) — so the actual processing (Gemini,
booking, the outbound Send API call) happens in a BackgroundTasks
callback (app/whatsapp_processing.py) AFTER this handler has already
decided to return 200, exactly like the pipeline documented there.
v1 deliberately uses BackgroundTasks only — no Redis/Celery/RQ/other
queue — so if the process restarts between "ack sent" and "processing
finished", that in-flight message is simply lost; there is no
persistent retry queue. This is a known, documented v1 limitation.

Never logs the raw webhook payload, the signature header, or any
secret — see the logging calls below and app/whatsapp_processing.py.
"""

import hashlib
import hmac
import json
import logging

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request, Response, status

from .. import config
from ..whatsapp_processing import process_incoming_message

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks/whatsapp", tags=["WhatsApp"])


def _valid_signature(raw_body: bytes, signature_header: str) -> bool:
    """Constant-time verification of Meta's X-Hub-Signature-256 header
    against the RAW request body. Fails closed: an unset
    WHATSAPP_APP_SECRET (e.g. WhatsApp not configured yet) means every
    POST is rejected, never accidentally accepted."""
    if not config.WHATSAPP_APP_SECRET:
        return False
    if not signature_header or not signature_header.startswith("sha256="):
        return False

    provided = signature_header[len("sha256="):]
    expected = hmac.new(
        config.WHATSAPP_APP_SECRET.encode("utf-8"), raw_body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, provided)


@router.get("")
def verify_whatsapp_webhook(
    hub_mode: str = Query(default="", alias="hub.mode"),
    hub_verify_token: str = Query(default="", alias="hub.verify_token"),
    hub_challenge: str = Query(default="", alias="hub.challenge"),
):
    """Meta's one-time webhook verification handshake. Must echo back
    hub.challenge verbatim on success — anything else on failure."""
    token_configured = bool(config.WHATSAPP_VERIFY_TOKEN)
    if (
        hub_mode == "subscribe"
        and token_configured
        and hmac.compare_digest(hub_verify_token, config.WHATSAPP_VERIFY_TOKEN)
    ):
        logger.info("WhatsApp webhook verification succeeded")
        return Response(content=hub_challenge, media_type="text/plain")

    logger.warning("WhatsApp webhook verification failed")
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Verification failed")


@router.post("")
async def receive_whatsapp_webhook(request: Request, background_tasks: BackgroundTasks):
    raw_body = await request.body()
    signature_header = request.headers.get("x-hub-signature-256", "")

    if not _valid_signature(raw_body, signature_header):
        logger.warning("WhatsApp webhook rejected: invalid or missing signature")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid signature")

    try:
        payload = json.loads(raw_body)
    except ValueError:
        logger.warning("WhatsApp webhook rejected: invalid JSON payload")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid payload")

    # Schedule every message found for background processing, then
    # return 200 immediately — see this module's docstring. Everything
    # after this point (including phone_number_id -> restaurant
    # resolution) happens in the background task, never here.
    scheduled = 0
    for entry in payload.get("entry", []) or []:
        for change in entry.get("changes", []) or []:
            value = change.get("value", {}) or {}
            phone_number_id = (value.get("metadata") or {}).get("phone_number_id")
            if not phone_number_id:
                continue
            for message in value.get("messages", []) or []:
                background_tasks.add_task(process_incoming_message, phone_number_id, message)
                scheduled += 1

    logger.info("WhatsApp webhook accepted (messages_scheduled=%s)", scheduled)
    return Response(status_code=status.HTTP_200_OK)
