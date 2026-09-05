"""
Chat API endpoint.

This is what the frontend widget calls every time the customer sends
a message. It:
  1. Looks up the restaurant's real data (knowledge.py)
  2. Sends it + the conversation to Gemini (llm.py), along with a
     booking tool handler (Stage 2c) that Gemini can call once it has
     conversationally gathered every required booking field
  3. Returns the reply

The tool handler below is the ONLY thing standing between "the model
decided to call create_booking" and an actual database write. It always
routes through app/booking.py's create_booking() — the same function
admin booking management uses — so there is exactly one place that
ever decides a booking succeeded or checks availability. This handler
just adapts Gemini's raw (untrusted) call arguments into that function
via the existing schemas.BookingCreate validation, and turns the
outcome into a small structured result for the model to relay; it never
writes anything itself and never claims success on its own authority.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import ValidationError
from sqlalchemy.orm import Session

from .. import models, schemas, knowledge, llm
from ..booking import BookingConflictError, create_booking
from ..database import get_db
from ..rate_limit import chat_rate_limiter

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/chat",
    tags=["chat"],
    dependencies=[Depends(chat_rate_limiter)],
)


def _summarize_validation_error(error: ValidationError) -> str:
    """A short, LLM-relayable summary of what was wrong with the booking
    arguments — not the raw Pydantic error (which includes internal
    type/URL noise not meant for an end user)."""
    parts = []
    for err in error.errors():
        field = ".".join(str(loc) for loc in err["loc"])
        parts.append(f"{field}: {err['msg']}")
    return "Invalid booking details — " + "; ".join(parts)


def _make_booking_tool_handler(db: Session, restaurant: models.Restaurant):
    """
    Builds the callback passed to llm.generate_reply(). Never raises —
    always returns a JSON-serialisable dict describing what happened,
    for the model to relay. Never logs customer_name/phone/email;
    create_booking() itself only logs id/date/time/party_size, per the
    project's no-PII-in-logs policy, and this function doesn't log at
    all beyond that.
    """

    def handle(args: dict) -> dict:
        try:
            data = schemas.BookingCreate(**args)
        except ValidationError as e:
            return {"status": "rejected", "reason": _summarize_validation_error(e)}

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


@router.post("", response_model=schemas.ChatResponse)
def chat(request: schemas.ChatRequest, db: Session = Depends(get_db)):
    try:
        restaurant = (
            db.query(models.Restaurant)
            .filter(models.Restaurant.id == request.restaurant_id)
            .first()
        )
        if not restaurant:
            raise HTTPException(status_code=404, detail="Restaurant not found")

        restaurant_context = knowledge.build_restaurant_context(db, request.restaurant_id)

        reply = llm.generate_reply(
            user_message=request.message,
            history=request.history,
            restaurant_context=restaurant_context,
            book_tool_handler=_make_booking_tool_handler(db, restaurant),
        )
        return schemas.ChatResponse(reply=reply)

    except HTTPException:
        raise
    except Exception:
        # Log the full error server-side (stack trace, Gemini error body,
        # etc.) but never expose that internal detail to the customer —
        # it could reveal implementation details or upstream error text.
        logger.exception("Unhandled error while generating a chat reply")
        raise HTTPException(
            status_code=500,
            detail="Sorry, something went wrong on our end. Please try again shortly.",
        )
