"""
Core WhatsApp message-processing pipeline (Stage 3 Step 6B).

Runs entirely inside a BackgroundTasks callback, AFTER
routers/whatsapp.py has already sent Meta its HTTP 200 ack — so nothing
here can delay that ack, and nothing here ever runs on the request's own
DB session (which may already be closed by the time this executes); it
opens and closes its own.

Pipeline, matching the approved Step 6B architecture exactly:
    phone_number_id -> restaurant resolution
    -> message deduplication
    -> WhatsApp conversation resolution (24-hour window; app/conversations.py)
    -> load persisted history
    -> Gemini reply generation (app/llm.py)
    -> shared booking tool handler (app/booking_tool.py — same as web chat)
    -> Meta Send API (app/whatsapp_client.py)
    -> persist the successful user + assistant turn

Every step that decides to stop processing a message (unknown
phone_number_id, duplicate message, invalid sender number, rate limit,
unsupported message type, Gemini failure, Send API failure) does so by
returning quietly and logging one operational-fact line — never by
raising an exception that would surface anywhere, since there is no
request left to surface it to.

The turn is committed to the database ONLY after send_text_message()
has itself succeeded (see the final block below). If Gemini fails, nothing
is sent and nothing is persisted. If the booking tool handler succeeds
(a real, committed booking — see app/booking_tool.py) but the LATER
Send API call then fails, the booking is real and is deliberately NOT
rolled back (there is no compensating-transaction mechanism, the same
tradeoff already accepted for web chat — see
tests/test_chat_persistence.py) — but the turn is never falsely
persisted, exactly mirroring web chat's own failure semantics.

v1 has no persistent retry queue (no Redis/Celery/RQ/other broker — see
routers/whatsapp.py). A message dropped at any step above is simply
lost; the operator finds out only via the logged operational fact, not
via any automatic retry.
"""

import logging
from typing import Optional

from fastapi import HTTPException

from . import conversations, knowledge, llm, models, whatsapp_client
from .booking_tool import make_booking_tool_handler
from .database import SessionLocal
from .phone import InvalidPhoneNumberError, normalize_whatsapp_phone
from .rate_limit import whatsapp_rate_limiter

logger = logging.getLogger(__name__)


def _resolve_restaurant(db, phone_number_id: str) -> Optional[models.Restaurant]:
    """The ONLY way a webhook's phone_number_id becomes a restaurant —
    an unrecognised phone_number_id returns None, which the caller
    treats as "drop this message", never "guess a restaurant"."""
    mapping = (
        db.query(models.WhatsAppNumber)
        .filter(models.WhatsAppNumber.phone_number_id == phone_number_id)
        .first()
    )
    if not mapping:
        return None
    return (
        db.query(models.Restaurant)
        .filter(models.Restaurant.id == mapping.restaurant_id)
        .first()
    )


def _is_duplicate_message(db, external_message_id: str) -> bool:
    return (
        db.query(models.Message)
        .filter(models.Message.external_message_id == external_message_id)
        .first()
        is not None
    )


def _extract_text(message: dict) -> Optional[str]:
    """Only plain text messages are handled in v1 (rich media, buttons,
    location, interactive lists are explicitly out of scope — see the
    Step 6B plan). Anything else returns None so the caller drops it."""
    if message.get("type") != "text":
        return None
    body = (message.get("text") or {}).get("body")
    if not body or not body.strip():
        return None
    return body


def process_incoming_message(phone_number_id: str, message: dict) -> None:
    """
    Entry point scheduled by routers/whatsapp.py's BackgroundTasks for
    each message in an already-signature-verified webhook payload.
    Never raises — every failure path logs and returns.
    """
    db = SessionLocal()
    try:
        restaurant = _resolve_restaurant(db, phone_number_id)
        if restaurant is None:
            logger.warning(
                "WhatsApp message dropped: unknown phone_number_id (phone_number_id=%s)",
                phone_number_id,
            )
            return

        external_message_id = message.get("id")
        if not external_message_id:
            logger.warning(
                "WhatsApp message dropped: missing message id (restaurant_id=%s)",
                restaurant.id,
            )
            return

        if _is_duplicate_message(db, external_message_id):
            logger.info(
                "WhatsApp duplicate message detected, skipping (restaurant_id=%s)",
                restaurant.id,
            )
            return

        raw_from = message.get("from")
        try:
            customer_phone = normalize_whatsapp_phone(raw_from)
        except InvalidPhoneNumberError:
            logger.warning(
                "WhatsApp message dropped: invalid sender phone number (restaurant_id=%s)",
                restaurant.id,
            )
            return

        rate_limit_key = f"{phone_number_id}:{customer_phone}"
        try:
            whatsapp_rate_limiter.check(rate_limit_key)
        except HTTPException:
            logger.warning(
                "WhatsApp message dropped: rate limit exceeded (restaurant_id=%s)",
                restaurant.id,
            )
            return

        text_body = _extract_text(message)
        if text_body is None:
            logger.info(
                "WhatsApp message dropped: unsupported message type (restaurant_id=%s)",
                restaurant.id,
            )
            return

        conversation, is_new = conversations.get_or_create_whatsapp_conversation(
            db, restaurant, customer_phone
        )
        history = conversations.load_history(db, conversation) if not is_new else []

        restaurant_context = knowledge.build_restaurant_context(db, restaurant.id)

        tool_call_flag: list = []
        try:
            reply = llm.generate_reply(
                user_message=text_body,
                history=history,
                restaurant_context=restaurant_context,
                book_tool_handler=make_booking_tool_handler(db, restaurant, tool_call_flag),
            )
        except Exception:
            # A booking tool call may already have committed inside
            # generate_reply() before a later failure — see this
            # module's docstring for why that is not rolled back, and
            # why the turn below is still never persisted.
            logger.exception(
                "WhatsApp: Gemini reply generation failed (restaurant_id=%s)",
                restaurant.id,
            )
            return

        try:
            whatsapp_client.send_text_message(phone_number_id, customer_phone, reply)
        except whatsapp_client.WhatsAppSendError:
            logger.warning(
                "WhatsApp: outbound send failed, turn not persisted (restaurant_id=%s)",
                restaurant.id,
            )
            return

        conversations.persist_turn(
            db, conversation,
            user_text=text_body,
            assistant_text=reply,
            triggered_tool_call=bool(tool_call_flag),
            external_message_id=external_message_id,
        )
        logger.info("WhatsApp message processed successfully (restaurant_id=%s)", restaurant.id)
    finally:
        db.close()
