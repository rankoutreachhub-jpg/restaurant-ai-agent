"""
Chat API endpoint.

This is what the frontend widget calls every time the customer sends
a message. It:
  1. Looks up the restaurant's real data (knowledge.py)
  2. Resolves (or creates) this conversation's persisted record
     (Stage 3 Step 5 — see app/conversations.py) and its history
  3. Sends it + the conversation to Gemini (llm.py), along with a
     booking tool handler (Stage 2c) that Gemini can call once it has
     conversationally gathered every required booking field
  4. Persists the exchange and returns the reply

The tool handler below is the ONLY thing standing between "the model
decided to call create_booking" and an actual database write. It always
routes through app/booking.py's create_booking() — the same function
admin booking management uses — so there is exactly one place that
ever decides a booking succeeded or checks availability. This handler
just adapts Gemini's raw (untrusted) call arguments into that function
via the existing schemas.BookingCreate validation, and turns the
outcome into a small structured result for the model to relay; it never
writes anything itself and never claims success on its own authority.

Conversation persistence never touches this contract: restaurant_id is
resolved server-side from the request body exactly as before, and the
LLM never sees or controls conversation identity — llm.py itself is
completely unmodified by Step 5 (no new parameters, no changed return
shape). The only new signal chat.py needs — whether the model actually
called the booking tool this turn — is captured via a small mutable
flag closed over by _make_booking_tool_handler below, not by changing
llm.py's interface.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import ValidationError
from sqlalchemy.orm import Session

from .. import conversations, models, schemas, knowledge, llm
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


def _make_booking_tool_handler(db: Session, restaurant: models.Restaurant, tool_call_flag: list):
    """
    Builds the callback passed to llm.generate_reply(). Never raises —
    always returns a JSON-serialisable dict describing what happened,
    for the model to relay. Never logs customer_name/phone/email;
    create_booking() itself only logs id/date/time/party_size, per the
    project's no-PII-in-logs policy, and this function doesn't log at
    all beyond that.

    tool_call_flag is a plain list used purely as a mutable out-param:
    appending to it when this handler actually runs is how chat.py
    learns "a tool call happened" without llm.py needing to say so
    itself (its return shape stays exactly what it always was).
    """

    def handle(args: dict) -> dict:
        tool_call_flag.append(True)
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
def chat(request: schemas.ChatRequest, response: Response, db: Session = Depends(get_db)):
    try:
        restaurant = (
            db.query(models.Restaurant)
            .filter(models.Restaurant.id == request.restaurant_id)
            .first()
        )
        if not restaurant:
            raise HTTPException(status_code=404, detail="Restaurant not found")

        # Resolving conversation identity happens entirely server-side,
        # from the request body's restaurant_id and (optional)
        # conversation_token — never anything the LLM sees or supplies.
        # An unknown or cross-restaurant token behaves identically to no
        # token at all: silently start a new conversation for THIS
        # restaurant, never an error, never a hint either way.
        conversation, is_new = conversations.get_or_create_conversation(
            db, restaurant, request.conversation_token
        )
        # Server-persisted history always wins on a real resume — a
        # client-supplied `history` is only trusted when there is no
        # persisted conversation to resume from (is_new), which is also
        # exactly today's pre-Step-5 behavior, byte for byte.
        history = conversations.load_history(db, conversation) if not is_new else (request.history or [])

        restaurant_context = knowledge.build_restaurant_context(db, request.restaurant_id)

        tool_call_flag: list = []
        reply = llm.generate_reply(
            user_message=request.message,
            history=history,
            restaurant_context=restaurant_context,
            book_tool_handler=_make_booking_tool_handler(db, restaurant, tool_call_flag),
        )

        # Only reached once a reply genuinely exists — never on any
        # exception path below, including the case where the booking
        # tool already succeeded (and committed) but a later Gemini call
        # then fails: that turn is deliberately never persisted, so the
        # conversation never falsely shows a saved exchange for a reply
        # the customer never received. The booking itself is NOT rolled
        # back — see tests/test_chat_persistence.py for why that's
        # correct given the existing booking architecture.
        conversations.persist_turn(
            db, conversation,
            user_text=request.message,
            assistant_text=reply,
            triggered_tool_call=bool(tool_call_flag),
        )
        response.headers["X-Conversation-Token"] = conversation.public_token

        return schemas.ChatResponse(reply=reply)

    except HTTPException:
        raise
    except Exception:
        # Log the full error server-side (stack trace, Gemini error body,
        # etc.) but never expose that internal detail to the customer —
        # it could reveal implementation details or upstream error text.
        # Never logs message content or the conversation token.
        logger.exception("Unhandled error while generating a chat reply")
        raise HTTPException(
            status_code=500,
            detail="Sorry, something went wrong on our end. Please try again shortly.",
        )
