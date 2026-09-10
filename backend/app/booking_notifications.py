"""
Booking confirmation email dispatch — runs as a FastAPI BackgroundTasks
callback, always AFTER the triggering request has already returned its
response (see routers/bookings.py and routers/chat.py, which schedule
send_confirmation_email_task only once a booking is already committed).

This module owns the decision of whether to actually send (the
idempotency check) and marks the booking as sent on success — it never
raises out of send_confirmation_email_task, so a failure here can never
surface to a client or affect a booking that has already committed.

Opens its own database session rather than reusing the triggering
request's — the same pattern app/whatsapp_processing.py already uses for
its own BackgroundTasks callback, for the same reason: a background task
can end up running independently of the request's session lifecycle.
"""

import logging
from datetime import datetime

from . import email_client, models
from .database import SessionLocal

logger = logging.getLogger(__name__)


def send_confirmation_email_task(booking_id: int) -> None:
    """
    Sends the confirmation email for one booking, if it hasn't already
    been sent. Never raises — every failure path (booking gone, email
    not configured, SMTP failure, anything unexpected) is logged and
    swallowed here, since this always runs after the booking's own
    transaction has already committed and after the HTTP response has
    already been decided; nothing about that can or should be undone by
    a notification failure.

    Never logs customer_name/email/phone/notes — only booking_id and the
    operational outcome, per this project's logging policy.
    """
    db = SessionLocal()
    try:
        booking = db.query(models.Booking).filter(models.Booking.id == booking_id).first()
        if booking is None:
            logger.warning("Booking confirmation email skipped: booking not found (booking_id=%s)", booking_id)
            return
        if booking.status != "confirmed":
            logger.info(
                "Booking confirmation email skipped: booking not in confirmed status (booking_id=%s)",
                booking_id,
            )
            return
        if booking.confirmation_sent_at is not None:
            # Idempotency guard: a duplicate/unintended second attempt at
            # scheduling this task for the same booking is a no-op.
            logger.info("Booking confirmation email skipped: already sent (booking_id=%s)", booking_id)
            return

        if not email_client.is_email_configured():
            logger.info("Booking confirmation email skipped: email not configured (booking_id=%s)", booking_id)
            return

        restaurant = db.query(models.Restaurant).filter(models.Restaurant.id == booking.restaurant_id).first()
        restaurant_name = restaurant.name if restaurant else "the restaurant"

        try:
            email_client.send_booking_confirmation_email(
                to_email=booking.email,
                restaurant_name=restaurant_name,
                booking_date=booking.booking_date.isoformat(),
                booking_time=booking.booking_time.isoformat(timespec="minutes"),
                party_size=booking.party_size,
                booking_id=booking.id,
            )
        except email_client.EmailSendError:
            # Already logged (operational facts only) inside email_client.
            # Deliberately not re-raised: this booking stays confirmed,
            # and no compensating action is taken, matching the same
            # accepted tradeoff app/whatsapp_client.py's callers already
            # make for a failed outbound send.
            return

        booking.confirmation_sent_at = datetime.utcnow()
        db.commit()
    except Exception:
        # Defence in depth: an unexpected error here (e.g. a DB error on
        # the background session) must never propagate — there is no
        # request left to surface it to, and the booking itself is
        # already safely committed regardless of what happens next.
        logger.exception("Unexpected error while sending booking confirmation email (booking_id=%s)", booking_id)
    finally:
        db.close()
