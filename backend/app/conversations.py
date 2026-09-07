"""
Conversation/message persistence (Stage 3 Step 5).

This is the single place that resolves which Conversation a /chat
request belongs to and the single place that writes Message rows —
mirroring app/booking.py's role as the one source of truth for booking
writes. routers/chat.py stays a thin caller; it never touches the
Conversation/Message tables directly.

Key security property: get_or_create_conversation requires BOTH
public_token and restaurant_id to match an existing row. A token that
is unknown, or that belongs to a different restaurant, is treated
identically to "no token supplied" — a brand-new conversation is
started. This is deliberate: a public, unauthenticated endpoint must
never distinguish "that token doesn't exist" from "that token exists
but isn't yours," since either response would leak information an
anonymous caller has no business learning.
"""

import secrets
from datetime import datetime, timedelta
from typing import List, Optional, Tuple

from sqlalchemy.orm import Session

from . import models, schemas

# Same high-entropy style as app/admin_keys.py's secret generation.
# Not an authentication credential — it identifies no user and grants
# no privilege — but it must be unguessable, since knowing it is what
# lets a request read/append a conversation's history on this public,
# unauthenticated surface.
PUBLIC_TOKEN_BYTES = 24

# Reuses the exact cap already enforced on client-supplied history
# today (schemas.CHAT_HISTORY_MAX_TURNS) — resuming from the database
# gives Gemini no larger a context window than the existing client-side
# contract already allowed.
MAX_RESUMED_TURNS = schemas.CHAT_HISTORY_MAX_TURNS

# Meta's WhatsApp "customer service window" (Stage 3 Step 6B): a
# business may only freely reply within 24 hours of the customer's last
# message. We mirror that here for our own conversation-grouping
# purposes (not for anything Meta enforces on our side) — a message
# arriving within 24 hours of the matching conversation's last activity
# resumes it; otherwise a new conversation starts, exactly as a new
# 24-hour session would on Meta's side.
WHATSAPP_CUSTOMER_SERVICE_WINDOW = timedelta(hours=24)


def _generate_public_token() -> str:
    return secrets.token_urlsafe(PUBLIC_TOKEN_BYTES)


def get_or_create_conversation(
    db: Session, restaurant: models.Restaurant, public_token: Optional[str]
) -> Tuple[models.Conversation, bool]:
    """
    Returns (conversation, is_new). If public_token is given and
    resolves to a real Conversation for THIS restaurant, that
    conversation is reused. Otherwise (no token, unknown token, or a
    token belonging to a different restaurant) a brand-new conversation
    is created and returned — never an error, never a hint about why.
    """
    if public_token:
        existing = (
            db.query(models.Conversation)
            .filter(
                models.Conversation.public_token == public_token,
                models.Conversation.restaurant_id == restaurant.id,
            )
            .first()
        )
        if existing:
            return existing, False

    conversation = models.Conversation(
        restaurant_id=restaurant.id,
        public_token=_generate_public_token(),
    )
    db.add(conversation)
    db.commit()
    db.refresh(conversation)
    return conversation, True


def get_or_create_whatsapp_conversation(
    db: Session, restaurant: models.Restaurant, customer_phone: str
) -> Tuple[models.Conversation, bool]:
    """
    The WhatsApp-specific counterpart to get_or_create_conversation
    above (Stage 3 Step 6B): resolves which Conversation an inbound
    WhatsApp message belongs to, applying Meta's 24-hour customer-
    service-window rule instead of a client-supplied token.

    customer_phone must already be normalized to E.164 (see
    app/phone.py) — this function does no normalization itself, only
    exact matching against Conversation.external_id.

    Timezone strategy: entirely UTC, via datetime.utcnow(), exactly like
    every other timestamp in this module and in models.py — there is no
    mixing of naive/aware datetimes anywhere in this comparison.

    Boundary semantics (deliberately explicit, not left to chance — see
    tests/test_whatsapp_conversations.py): a conversation last active
    EXACTLY 24 hours ago (updated_at == window_start, to the second) is
    still considered within the window (the comparison is >=), so it is
    resumed; anything older starts a new conversation.
    """
    window_start = datetime.utcnow() - WHATSAPP_CUSTOMER_SERVICE_WINDOW
    existing = (
        db.query(models.Conversation)
        .filter(
            models.Conversation.restaurant_id == restaurant.id,
            models.Conversation.channel == "whatsapp",
            models.Conversation.external_id == customer_phone,
            models.Conversation.updated_at >= window_start,
        )
        .order_by(models.Conversation.updated_at.desc())
        .first()
    )
    if existing:
        return existing, False

    conversation = models.Conversation(
        restaurant_id=restaurant.id,
        public_token=_generate_public_token(),
        channel="whatsapp",
        external_id=customer_phone,
    )
    db.add(conversation)
    db.commit()
    db.refresh(conversation)
    return conversation, True


def load_history(db: Session, conversation: models.Conversation) -> List[schemas.ChatMessage]:
    """
    Loads this conversation's persisted messages, most recent
    MAX_RESUMED_TURNS, oldest-first — the same ordering and shape
    llm.generate_reply() already expects from client-supplied history.
    """
    rows = (
        db.query(models.Message)
        .filter(models.Message.conversation_id == conversation.id)
        .order_by(models.Message.created_at.desc())
        .limit(MAX_RESUMED_TURNS)
        .all()
    )
    rows.reverse()
    return [schemas.ChatMessage(role=row.role, content=row.content) for row in rows]


def persist_turn(
    db: Session,
    conversation: models.Conversation,
    user_text: str,
    assistant_text: str,
    triggered_tool_call: bool,
    external_message_id: Optional[str] = None,
) -> None:
    """
    Persists one exchange (one user message, one assistant reply) and
    bumps the conversation's updated_at. Called ONLY after a reply has
    actually been produced — routers/chat.py never calls this on any
    error path, so a turn that failed (e.g. the booking-succeeds-but-
    final-reply-fails case; see tests/test_chat_persistence.py) never
    appears as persisted, and the conversation's updated_at never
    advances on a turn that produced no persisted content. For WhatsApp
    (Stage 3 Step 6B), app/whatsapp_processing.py additionally never
    calls this until the outbound Send API call has itself succeeded —
    see that module for why.

    external_message_id (Stage 3 Step 6B) is Meta's id for the inbound
    customer message that produced this turn — recorded on the "user"
    row only (there is no equivalent id for our own reply) so a
    redelivered webhook event can be recognised as a duplicate before
    ever reaching this function again. None for web chat, which has no
    such external id.
    """
    now = datetime.utcnow()
    db.add(models.Message(
        conversation_id=conversation.id, role="user", content=user_text,
        external_message_id=external_message_id, created_at=now,
    ))
    db.add(models.Message(
        conversation_id=conversation.id, role="assistant", content=assistant_text,
        triggered_tool_call=triggered_tool_call, created_at=now,
    ))
    conversation.updated_at = now
    db.commit()
