"""
Read-only admin access to persisted conversations (Stage 3 Step 5).

Protected identically to every other restaurant-scoped admin resource:
get_current_admin + require_restaurant_access, reused verbatim — no new
authentication or authorization mechanism. The public_token is never
exposed here; admins address a conversation by its plain integer id,
exactly like every other admin resource's URL convention. Only GET
endpoints exist — there is no legitimate reason for an admin to edit or
delete a customer's message, and retention/deletion is a deliberately
separate, future concern (see README, "Conversation persistence").
"""

from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from .. import models, schemas
from ..auth import AdminIdentity, get_current_admin
from ..authz import require_restaurant_access
from ..database import get_db
from ..rate_limit import admin_rate_limiter

router = APIRouter(
    prefix="/admin",
    tags=["Admin - Conversations"],
    dependencies=[Depends(admin_rate_limiter)],
)

DEFAULT_PAGE_LIMIT = 50
MAX_PAGE_LIMIT = 200


def _get_conversation_or_404(db: Session, restaurant_id: int, conversation_id: int) -> models.Conversation:
    conversation = (
        db.query(models.Conversation)
        .filter(
            models.Conversation.id == conversation_id,
            models.Conversation.restaurant_id == restaurant_id,
        )
        .first()
    )
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return conversation


@router.get("/restaurant/{restaurant_id}/conversations", response_model=List[schemas.ConversationOut])
def list_conversations(
    restaurant_id: int,
    limit: int = Query(default=DEFAULT_PAGE_LIMIT, ge=1, le=MAX_PAGE_LIMIT),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    current_admin: AdminIdentity = Depends(get_current_admin),
):
    require_restaurant_access(restaurant_id, db, current_admin)

    return (
        db.query(models.Conversation)
        .filter(models.Conversation.restaurant_id == restaurant_id)
        .order_by(models.Conversation.updated_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )


@router.get(
    "/restaurant/{restaurant_id}/conversations/{conversation_id}/messages",
    response_model=List[schemas.MessageOut],
)
def list_conversation_messages(
    restaurant_id: int,
    conversation_id: int,
    limit: int = Query(default=DEFAULT_PAGE_LIMIT, ge=1, le=MAX_PAGE_LIMIT),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    current_admin: AdminIdentity = Depends(get_current_admin),
):
    require_restaurant_access(restaurant_id, db, current_admin)
    conversation = _get_conversation_or_404(db, restaurant_id, conversation_id)

    return (
        db.query(models.Message)
        .filter(models.Message.conversation_id == conversation.id)
        .order_by(models.Message.created_at.asc())
        .offset(offset)
        .limit(limit)
        .all()
    )
