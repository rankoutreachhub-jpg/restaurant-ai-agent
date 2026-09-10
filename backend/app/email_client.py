"""
Resend integration: sending a booking confirmation email.

This is the only place that talks to Resend's API. It never decides
WHETHER to send or WHAT booking to send for — app/booking_notifications.py
owns that decision (including the idempotency check) and supplies the
already-decided booking details. Mirrors app/whatsapp_client.py's shape
deliberately: a single send function, a typed exception carrying no
message content or credentials, and logging limited to the operational
fact of success/failure — the same pattern this module used for its
previous SMTP transport, unchanged by this migration.

Uses httpx (already a dependency — see app/whatsapp_client.py, which
calls Meta's Graph API the same way) to POST directly to Resend's REST
API, rather than adding the `resend` package as a new dependency.
"""

import logging

import httpx

from . import config

logger = logging.getLogger(__name__)

SEND_TIMEOUT_SECONDS = 10.0

RESEND_API_URL = "https://api.resend.com/emails"


class EmailSendError(Exception):
    """Raised when sending the confirmation email fails for any reason
    (not configured, network error, non-2xx response). Carries no
    recipient address, message content, or credentials — see the
    logging call below for why."""


def is_email_configured() -> bool:
    """True once both RESEND_API_KEY and EMAIL_FROM are set — the
    minimum needed to attempt a send. Checked by
    booking_notifications.py before ever constructing a request, so an
    unconfigured deployment never calls out to Resend at all."""
    return bool(config.RESEND_API_KEY and config.EMAIL_FROM)


def send_booking_confirmation_email(
    *,
    to_email: str,
    restaurant_name: str,
    booking_date: str,
    booking_time: str,
    party_size: int,
    booking_id: int,
) -> None:
    """
    Sends a plain-text booking confirmation email via Resend's HTTPS
    API. Raises EmailSendError on any failure, including "not
    configured" — callers must treat failure as a hard stop for this
    attempt (app/booking_notifications.py logs and moves on; it never
    lets this affect the booking itself).

    Never logs `to_email` (customer email address) or any message
    content — only the operational fact of success/failure, per this
    project's logging policy (see app/logging_config.py and the same
    convention in app/whatsapp_client.py).
    """
    if not is_email_configured():
        raise EmailSendError("Email sending is not configured")

    subject = f"Booking confirmed at {restaurant_name}"
    text = (
        f"Hi,\n\n"
        f"Your table booking at {restaurant_name} is confirmed:\n\n"
        f"  Date: {booking_date}\n"
        f"  Time: {booking_time}\n"
        f"  Party size: {party_size}\n\n"
        f"Booking reference: #{booking_id}\n\n"
        f"If anything about this booking needs to change, please contact "
        f"the restaurant directly.\n\n"
        f"See you soon!\n"
    )

    headers = {"Authorization": f"Bearer {config.RESEND_API_KEY}"}
    payload = {
        "from": config.EMAIL_FROM,
        "to": [to_email],
        "subject": subject,
        "text": text,
    }

    try:
        response = httpx.post(RESEND_API_URL, headers=headers, json=payload, timeout=SEND_TIMEOUT_SECONDS)
        response.raise_for_status()
    except httpx.HTTPError:
        logger.warning("Booking confirmation email send failed (booking_id=%s)", booking_id)
        raise EmailSendError("Resend API call failed") from None

    logger.info("Booking confirmation email sent (booking_id=%s)", booking_id)
