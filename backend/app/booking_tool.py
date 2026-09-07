"""
Shared AI-assisted booking tool handler.

Originally lived inline in routers/chat.py (Stage 2c, web chat only).
Stage 3 Step 6B extracts it here, unchanged, so WhatsApp
(app/whatsapp_processing.py) and web chat (routers/chat.py) call exactly
the same implementation — there is exactly one place that adapts
Gemini's raw (untrusted) tool-call arguments into a real booking
attempt, and it always routes through app/booking.py's create_booking(),
the same function admin booking management uses. This module never
writes anything itself and never claims success on its own authority.
"""

from pydantic import ValidationError
from sqlalchemy.orm import Session

from . import models, schemas
from .booking import BookingConflictError, create_booking


def summarize_validation_error(error: ValidationError) -> str:
    """A short, LLM-relayable summary of what was wrong with the booking
    arguments — not the raw Pydantic error (which includes internal
    type/URL noise not meant for an end user)."""
    parts = []
    for err in error.errors():
        field = ".".join(str(loc) for loc in err["loc"])
        parts.append(f"{field}: {err['msg']}")
    return "Invalid booking details — " + "; ".join(parts)


def make_booking_tool_handler(db: Session, restaurant: models.Restaurant, tool_call_flag: list):
    """
    Builds the callback passed to llm.generate_reply(). Never raises —
    always returns a JSON-serialisable dict describing what happened,
    for the model to relay. Never logs customer_name/phone/email;
    create_booking() itself only logs id/date/time/party_size, per the
    project's no-PII-in-logs policy, and this function doesn't log at
    all beyond that.

    tool_call_flag is a plain list used purely as a mutable out-param:
    appending to it when this handler actually runs is how the caller
    learns "a tool call happened" without llm.py needing to say so
    itself (its return shape stays exactly what it always was).
    """

    def handle(args: dict) -> dict:
        tool_call_flag.append(True)
        try:
            data = schemas.BookingCreate(**args)
        except ValidationError as e:
            return {"status": "rejected", "reason": summarize_validation_error(e)}

        try:
            booking = create_booking(db, restaurant, data)
        except BookingConflictError as e:
            return {"status": "rejected", "reason": str(e)}

        return {
            "status": "confirmed",
            "booking_id": booking.id,
            "booking_date": booking.booking_date.isoformat(),
            "booking_time": booking.booking_time.isoformat(),
            "party_size": booking.party_size,
        }

    return handle
