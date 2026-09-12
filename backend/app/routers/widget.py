"""
Public widget endpoints (Stage 4 — Production Customer Widget).

GET /widget/{widget_key}/config (Phase B) and POST /widget/{widget_key}/chat
(Phase C) are this project's only genuinely public, unauthenticated
endpoints beyond legacy /chat: no X-Admin-API-Key, no admin_rate_limiter.
Both only ever act on a restaurant's PUBLIC branding/config
(app/models.py:WidgetConfig) — never anything from the admin surface,
and never the internal restaurant_id.

Security shape, mirroring app/authz.py's existing "don't let a 403
confirm existence" reasoning: an unknown widget_key and a known-but-
paused one (is_active=False) return the exact same generic 404 on
BOTH routes below — both conditions are checked in a single query
predicate, so there is one code path, not two branches that could
drift apart and start leaking which case actually applied.

CORS is deliberately unchanged in Phase C: these routes sit under the
existing app-wide CORSMiddleware (ALLOWED_ORIGINS) like every other
route in this app, not yet under any per-widget allowed-origin logic —
a later phase's concern, not this one's.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

from .. import conversations, knowledge, llm, models, schemas
from ..booking_tool import make_booking_tool_handler
from ..database import get_db
from ..rate_limit import widget_chat_rate_limiter, widget_config_rate_limiter

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/widget", tags=["Widget (public)"])

_NOT_FOUND = HTTPException(status_code=404, detail="Widget not found")


def _get_active_widget_config_or_404(db: Session, widget_key: str) -> models.WidgetConfig:
    config = (
        db.query(models.WidgetConfig)
        .filter(
            models.WidgetConfig.widget_key == widget_key,
            models.WidgetConfig.is_active,
        )
        .first()
    )
    if not config:
        raise _NOT_FOUND
    return config


@router.get(
    "/{widget_key}/config",
    response_model=schemas.WidgetPublicConfigOut,
    dependencies=[Depends(widget_config_rate_limiter)],
)
def get_public_widget_config(widget_key: str, db: Session = Depends(get_db)):
    config = _get_active_widget_config_or_404(db, widget_key)
    restaurant = config.restaurant

    return schemas.WidgetPublicConfigOut(
        widget_key=config.widget_key,
        restaurant_name=restaurant.name,
        welcome_message=config.welcome_message or f"Hiya! Welcome to {restaurant.name}.",
        primary_language=config.primary_language,
        logo_url=config.logo_url,
        accent_color=config.accent_color,
        booking_enabled=config.booking_enabled,
    )


# =========================================================
# WIDGET CHAT (Stage 4 Phase C)
# =========================================================
# Deliberately mirrors routers/chat.py's shape as closely as possible —
# same conversation-persistence primitives, same shared booking tool
# handler, same "persist only after a reply genuinely exists" rule —
# with exactly one difference in identity resolution: tenant comes
# SOLELY from widget_key (never a client-supplied restaurant_id, which
# this endpoint's request schema doesn't even have a field for), and
# the conversation-resume token travels as a request header
# (X-Conversation-Token) instead of a request-body field, since
# widget_key already occupies the URL and there is no legacy
# body-field contract to preserve here the way there is for /chat.

@router.post(
    "/{widget_key}/chat",
    response_model=schemas.ChatResponse,
    dependencies=[Depends(widget_chat_rate_limiter)],
)
def widget_chat(
    widget_key: str,
    request: schemas.WidgetChatRequest,
    http_request: Request,
    response: Response,
    db: Session = Depends(get_db),
):
    try:
        config = _get_active_widget_config_or_404(db, widget_key)
        restaurant = config.restaurant

        incoming_token = http_request.headers.get("X-Conversation-Token")
        conversation, is_new = conversations.get_or_create_conversation(
            db, restaurant, incoming_token, channel="widget"
        )
        history = conversations.load_history(db, conversation) if not is_new else []

        restaurant_context = knowledge.build_restaurant_context(db, restaurant.id)

        tool_call_flag: list = []
        book_tool_handler = (
            make_booking_tool_handler(db, restaurant, tool_call_flag)
            if config.booking_enabled
            else None
        )

        reply = llm.generate_reply(
            user_message=request.message,
            history=history,
            restaurant_context=restaurant_context,
            book_tool_handler=book_tool_handler,
        )

        # Only reached once a reply genuinely exists — see
        # routers/chat.py's identical comment for why a turn that failed
        # (including the booking-succeeds-but-final-reply-fails case) is
        # deliberately never persisted, and why the booking itself is
        # NOT rolled back.
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
        # Same discipline as routers/chat.py: log the full error
        # server-side, never expose internal detail to the customer,
        # never log message content or the conversation token.
        logger.exception("Unhandled error while generating a widget chat reply")
        raise HTTPException(
            status_code=500,
            detail="Sorry, something went wrong on our end. Please try again shortly.",
        )
