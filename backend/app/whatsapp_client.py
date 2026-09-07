"""
Meta Graph API integration: sending an outbound WhatsApp text message
(Stage 3 Step 6B).

This is the only place that calls out to Meta's Send API. It never
decides what to send — app/whatsapp_processing.py supplies the already-
generated Gemini reply — and it never retries: a failure here is
reported to the caller (WhatsAppSendError) exactly once, and the
documented v1 limitation is that there is no persistent retry queue (no
Redis/Celery/RQ — see app/whatsapp_processing.py).

Tests never make a real Meta API call — they monkeypatch
send_text_message directly (see tests/test_whatsapp_*.py), the same
pattern already used for llm_module.client.models.generate_content.
"""

import logging

import httpx

from . import config

logger = logging.getLogger(__name__)

GRAPH_API_BASE = "https://graph.facebook.com"
SEND_TIMEOUT_SECONDS = 10.0


class WhatsAppSendError(Exception):
    """Raised when the outbound Send API call fails for any reason
    (network error, non-2xx response, timeout). Carries no message
    content or token — see the logging call below for why."""


def send_text_message(phone_number_id: str, to: str, text: str) -> None:
    """
    Sends a plain text WhatsApp message via the Meta Graph API. Raises
    WhatsAppSendError on any failure — never returns a falsy "it
    probably failed" value, since app/whatsapp_processing.py must treat
    failure as a hard stop (the turn is not persisted; see that module).

    Never logs `text` (message content) or `to` (customer phone number)
    — only the operational fact of success/failure, per the project's
    logging policy (see app/logging_config.py).
    """
    url = f"{GRAPH_API_BASE}/{config.WHATSAPP_API_VERSION}/{phone_number_id}/messages"
    headers = {"Authorization": f"Bearer {config.WHATSAPP_ACCESS_TOKEN}"}
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": text},
    }

    try:
        response = httpx.post(url, headers=headers, json=payload, timeout=SEND_TIMEOUT_SECONDS)
        response.raise_for_status()
    except httpx.HTTPError:
        logger.warning("WhatsApp Send API call failed (phone_number_id=%s)", phone_number_id)
        raise WhatsAppSendError("WhatsApp Send API call failed") from None

    logger.info("WhatsApp Send API call succeeded (phone_number_id=%s)", phone_number_id)
