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
from datetime import datetime
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
) -> None:
    """
    Persists one exchange (one user message, one assistant reply) and
    bumps the conversation's updated_at. Called ONLY after a reply has
    actually been produced — routers/chat.py never calls this on any
    error path, so a turn that failed (e.g. the booking-succeeds-but-
    final-reply-fails case; see tests/test_chat_persistence.py) never
    appears as persisted, and the conversation's updated_at never
    advances on a turn that produced no persisted content.
    """
    now = datetime.utcnow()
    db.add(models.Message(conversation_id=conversation.id, role="user", content=user_text, created_at=now))
    db.add(models.Message(
        conversation_id=conversation.id, role="assistant", content=assistant_text,
        triggered_tool_call=triggered_tool_call, created_at=now,
    ))
    conversation.updated_at = now
    db.commit()
