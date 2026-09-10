"""
SMTP integration: sending a booking confirmation email.

This is the only place that talks to an SMTP server. It never decides
WHETHER to send or WHAT booking to send for — app/booking_notifications.py
owns that decision (including the idempotency check) and supplies the
already-decided booking details. Mirrors app/whatsapp_client.py's shape
deliberately: a single send function, a typed exception carrying no
message content or credentials, and logging limited to the operational
fact of success/failure.

Uses only the standard library (smtplib + email.message.EmailMessage) —
no new dependency — so it works against any SMTP-capable provider (a
managed relay or a real mail server) without being tied to one vendor's
API.

TLS is always mandatory, never optional or best-effort, but providers
differ on which of the two standard TLS modes they expect, chosen by
port — see IMPLICIT_TLS_PORT and send_booking_confirmation_email below:
  - Implicit TLS/SMTPS (conventionally port 465): the connection is
    TLS-wrapped from the first byte (smtplib.SMTP_SSL).
  - STARTTLS (conventionally port 587, or 25 with STARTTLS support):
    connect in plaintext, then upgrade with starttls() before anything
    sensitive is sent (smtplib.SMTP).
"""

import logging
import smtplib
from email.message import EmailMessage

from . import config

logger = logging.getLogger(__name__)

SEND_TIMEOUT_SECONDS = 10.0

# The conventional port for implicit TLS/SMTPS (the connection is
# TLS-wrapped from the very first byte, via smtplib.SMTP_SSL). Any other
# port is treated as STARTTLS (smtplib.SMTP, then upgraded with
# starttls() before anything sensitive is sent) — see
# send_booking_confirmation_email below. TLS is mandatory either way;
# this only decides WHICH of the two TLS handshakes a given port needs.
IMPLICIT_TLS_PORT = 465


class EmailSendError(Exception):
    """Raised when sending the confirmation email fails for any reason
    (not configured, connection error, auth failure, SMTP error). Carries
    no recipient address, message content, or credentials — see the
    logging call below for why."""


def is_email_configured() -> bool:
    """True once both SMTP_HOST and EMAIL_FROM are set — the minimum
    needed to attempt a send. Checked by booking_notifications.py before
    ever constructing a message, so an unconfigured deployment never
    touches smtplib at all."""
    return bool(config.SMTP_HOST and config.EMAIL_FROM)


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
    Sends a plain-text booking confirmation email. Raises EmailSendError
    on any failure, including "not configured" — callers must treat
    failure as a hard stop for this attempt (app/booking_notifications.py
    logs and moves on; it never lets this affect the booking itself).

    Never logs `to_email` (customer email address) or any message
    content — only the operational fact of success/failure, per this
    project's logging policy (see app/logging_config.py and the same
    convention in app/whatsapp_client.py).
    """
    if not is_email_configured():
        raise EmailSendError("Email sending is not configured")

    message = EmailMessage()
    message["Subject"] = f"Booking confirmed at {restaurant_name}"
    message["From"] = config.EMAIL_FROM
    message["To"] = to_email
    message.set_content(
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

    # Two supported TLS modes, chosen by port — both mandatory TLS, never
    # a plaintext fallback:
    #   - IMPLICIT_TLS_PORT (465, "SMTPS"): the socket is TLS-wrapped
    #     before any SMTP command is sent, via SMTP_SSL. Calling
    #     starttls() on a connection like this would fail (the server
    #     already expects TLS, not a plaintext STARTTLS negotiation).
    #   - any other port (587 conventionally, or 25 with STARTTLS
    #     support): connect in plaintext, then unconditionally upgrade
    #     with starttls() before login()/send_message() — if the server
    #     can't upgrade, starttls() raises and nothing sensitive is ever
    #     sent in the clear.
    use_implicit_tls = config.SMTP_PORT == IMPLICIT_TLS_PORT
    smtp_class = smtplib.SMTP_SSL if use_implicit_tls else smtplib.SMTP

    try:
        with smtp_class(config.SMTP_HOST, config.SMTP_PORT, timeout=SEND_TIMEOUT_SECONDS) as smtp:
            if not use_implicit_tls:
                smtp.starttls()
            if config.SMTP_USER:
                smtp.login(config.SMTP_USER, config.SMTP_PASSWORD)
            smtp.send_message(message)
    except (smtplib.SMTPException, OSError):
        logger.warning("Booking confirmation email send failed (booking_id=%s)", booking_id)
        raise EmailSendError("SMTP send failed") from None

    logger.info("Booking confirmation email sent (booking_id=%s)", booking_id)
