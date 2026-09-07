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

The booking tool handler (Stage 3 Step 6B: extracted into
app/booking_tool.py so WhatsApp uses exactly the same implementation)
is the ONLY thing standing between "the model decided to call
create_booking" and an actual database write. It always routes through
app/booking.py's create_booking() — the same function admin booking
management uses — so there is exactly one place that ever decides a
booking succeeded or checks availability. It just adapts Gemini's raw
(untrusted) call arguments into that function via the existing
schemas.BookingCreate validation, and turns the outcome into a small
structured result for the model to relay; it never writes anything
itself and never claims success on its own authority.

Conversation persistence never touches this contract: restaurant_id is
resolved server-side from the request body exactly as before, and the
LLM never sees or controls conversation identity — llm.py itself is
completely unmodified by Step 5 (no new parameters, no changed return
shape). The only new signal chat.py needs — whether the model actually
called the booking tool this turn — is captured via a small mutable
flag closed over by make_booking_tool_handler (app/booking_tool.py),
not by changing llm.py's interface.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from .. import conversations, models, schemas, knowledge, llm
from ..booking_tool import make_booking_tool_handler
from ..database import get_db
from ..rate_limit import chat_rate_limiter

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/chat",
    tags=["chat"],
    dependencies=[Depends(chat_rate_limiter)],
)


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
            book_tool_handler=make_booking_tool_handler(db, restaurant, tool_call_flag),
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
