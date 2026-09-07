"""
Strict per-restaurant CORS enforcement for the public widget surface
(Stage 4 Phase D).

Why this exists as its own middleware rather than an extension of the
app-wide CORSMiddleware in app/main.py: a SaaS widget can be embedded
on any restaurant's own website, unknown in advance — a single,
operator-configured ALLOWED_ORIGINS list (as app/main.py's existing
CORSMiddleware uses for /admin, /chat, etc.) fundamentally can't
pre-register every customer's domain the same way a single-tenant
app's frontend origin is pre-registered. So each restaurant's widget
gets its OWN allowed-origins set (app/models.py:WidgetAllowedOrigin,
managed via app/routers/admin.py), checked here per request.

IMPORTANT — this does NOT replace or reconfigure the existing
CORSMiddleware. That middleware intercepts preflight OPTIONS requests
for the ENTIRE app with no path-scoping at all (confirmed empirically
during Phase D planning), so simply mounting /widget on a separate
sub-app would NOT isolate its CORS handling — the parent's own
CORSMiddleware would still answer /widget/* preflight requests first,
using the wrong (global) policy. The actual fix: this middleware is
registered in app/main.py AFTER the existing CORSMiddleware, which
makes it the OUTERMOST layer — for any path starting with /widget/, it
fully owns the CORS decision (including preflight) and never calls
onward to the global CORSMiddleware; for every other path, it's a pure
pass-through, so /admin, /admin/platform, legacy /chat, WhatsApp, and
/health all still reach the untouched, unmodified global CORSMiddleware
exactly as before this phase.

Security shape:
  - Origin is never tenant identity — widget_key is resolved first,
    exactly like the route handlers themselves already do (see
    app/routers/widget.py); Origin is only ever checked AGAINST an
    already-resolved widget's own configured set.
  - Unknown/inactive widget_key: no CORS decision to make for a real
    GET/POST request — pass through untouched so the route's own
    existing, already-tested 404 fires identically either way (the one
    exception is OPTIONS, which no route handles at all, so this
    middleware answers it directly with the SAME generic 404 body).
  - Zero configured origins, or a configured-but-non-matching origin:
    both fail closed identically (403, no CORS headers) — a browser
    Origin header present with nothing that validates it is always a
    hard no, never a soft pass-through.
  - No Origin header at all: not a cross-origin browser request (curl,
    server-to-server, this project's own tests) — proceeds untouched.
  - Access-Control-Allow-Credentials is never set — this surface has
    and needs no cookies, matching the rest of this project's
    allow_credentials=False posture.
"""

import logging

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from . import config, models
from .database import SessionLocal
from .routers.widget import _NOT_FOUND

logger = logging.getLogger(__name__)

_WIDGET_PATH_PREFIX = "/widget/"
_ALLOWED_METHODS = "GET, POST, OPTIONS"
_ALLOWED_HEADERS = "Content-Type, X-Conversation-Token"
_EXPOSED_HEADERS = "X-Conversation-Token"
_MAX_AGE = "600"


def _extract_widget_key(path: str):
    if not path.startswith(_WIDGET_PATH_PREFIX):
        return None
    remainder = path[len(_WIDGET_PATH_PREFIX):]
    segments = remainder.split("/", 1)
    widget_key = segments[0] if segments else ""
    return widget_key or None


def _load_allowed_origins(widget_key: str):
    """
    Returns None if the widget_key doesn't resolve to an active
    WidgetConfig at all — the caller treats that as "not this
    middleware's decision to make" (see module docstring). Otherwise
    returns the (possibly empty) set of its configured allowed origins.

    Opens its own short-lived session rather than using
    Depends(get_db) — middleware runs outside FastAPI's normal
    dependency-injection scope, the same reason
    app/whatsapp_processing.py manages its own session.
    """
    db = SessionLocal()
    try:
        widget_config = (
            db.query(models.WidgetConfig)
            .filter(models.WidgetConfig.widget_key == widget_key, models.WidgetConfig.is_active)
            .first()
        )
        if not widget_config:
            return None
        return {row.origin for row in widget_config.allowed_origins}
    finally:
        db.close()


def _is_origin_allowed(origin: str, allowed_origins: set) -> bool:
    if origin in allowed_origins:
        return True
    # A single platform-level trusted origin, permitted against ANY
    # widget_key — see config.WIDGET_PREVIEW_ORIGIN's own docstring for
    # why this is currently inert (nothing calls from it yet).
    return bool(config.WIDGET_PREVIEW_ORIGIN) and origin == config.WIDGET_PREVIEW_ORIGIN


def _not_found_response() -> Response:
    # Byte-for-byte the same body FastAPI would render for the route
    # handlers' own _NOT_FOUND — imported directly, not retyped, so the
    # two can never silently drift apart.
    return JSONResponse(status_code=404, content={"detail": _NOT_FOUND.detail})


def _forbidden_response() -> Response:
    return Response(status_code=403)


async def widget_cors_middleware(request: Request, call_next):
    widget_key = _extract_widget_key(request.url.path)
    if widget_key is None:
        # Not a /widget/* request — untouched, falls through to the
        # existing global CORSMiddleware exactly as before this phase.
        return await call_next(request)

    allowed_origins = await run_in_threadpool(_load_allowed_origins, widget_key)
    origin = request.headers.get("origin")

    if request.method == "OPTIONS":
        # No route handles OPTIONS at all, so this middleware must
        # answer it completely, for every outcome.
        if allowed_origins is None:
            return _not_found_response()
        if origin and _is_origin_allowed(origin, allowed_origins):
            return Response(
                status_code=200,
                headers={
                    "Access-Control-Allow-Origin": origin,
                    "Vary": "Origin",
                    "Access-Control-Allow-Methods": _ALLOWED_METHODS,
                    "Access-Control-Allow-Headers": _ALLOWED_HEADERS,
                    "Access-Control-Max-Age": _MAX_AGE,
                },
            )
        return _forbidden_response()

    if allowed_origins is None:
        # Unknown/inactive widget: no CORS decision to make — let the
        # real route's own existing 404 logic handle it identically to
        # how it already does for both GET /config and POST /chat.
        return await call_next(request)

    if origin is None:
        # Non-browser caller — nothing to validate, proceed untouched.
        return await call_next(request)

    if not _is_origin_allowed(origin, allowed_origins):
        # Covers BOTH "zero configured" (fail closed) and "configured
        # but no match" — identical outcome either way, no CORS headers.
        return _forbidden_response()

    response = await call_next(request)
    response.headers["Access-Control-Allow-Origin"] = origin
    response.headers["Vary"] = "Origin"
    response.headers["Access-Control-Expose-Headers"] = _EXPOSED_HEADERS
    return response
