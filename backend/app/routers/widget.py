"""
Public widget endpoints (Stage 4 Phase B — Production Customer Widget).

GET /widget/{widget_key}/config is the first genuinely public,
unauthenticated endpoint this project exposes beyond /chat: no
X-Admin-API-Key, no admin_rate_limiter. It only ever returns a
restaurant's PUBLIC branding/config (app/models.py:WidgetConfig) — never
anything from the admin surface, and never the internal restaurant_id.

Security shape, mirroring app/authz.py's existing "don't let a 403
confirm existence" reasoning: an unknown widget_key and a known-but-
paused one (is_active=False) return the exact same generic 404 — both
conditions are checked in a single query predicate below, so there is
one code path, not two branches that could drift apart and start
leaking which case actually applied.

Rate limiting is deliberately NOT added here — see Phase C, which
covers widget-specific rate limiting on the (Gemini-cost-driving) chat
endpoint this router will eventually also host. CORS is deliberately
unchanged: this route sits under the existing app-wide CORSMiddleware
(ALLOWED_ORIGINS) like every other route in this app, not yet under any
per-widget allowed-origin logic — a later phase's concern, not this
one's.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import models, schemas
from ..database import get_db

router = APIRouter(prefix="/widget", tags=["Widget (public)"])

_NOT_FOUND = HTTPException(status_code=404, detail="Widget not found")


@router.get("/{widget_key}/config", response_model=schemas.WidgetPublicConfigOut)
def get_public_widget_config(widget_key: str, db: Session = Depends(get_db)):
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
