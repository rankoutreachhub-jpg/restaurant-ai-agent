"""
Main FastAPI application.

Run this with:
    uvicorn app.main:app --reload

from inside the backend/ folder.
"""

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.orm import Session
from starlette.staticfiles import StaticFiles

from . import config
from .database import SessionLocal, get_db
from .logging_config import configure_logging
from .monitoring import init_sentry
from .routers import chat, admin, bookings, platform_admin, conversations, whatsapp, widget
from .security_headers import security_headers_middleware
from .seed_data import seed_if_empty
from .widget_cors import widget_cors_middleware

logger = logging.getLogger(__name__)

# Console + rotating log file for errors and important events (see
# app/logging_config.py) — set up before anything else logs, so nothing
# is missed.
configure_logging()

# A no-op unless SENTRY_DSN is set — see app/monitoring.py for the full
# privacy/scrubbing design (Production Readiness Audit BLOCKER #1).
init_sentry()

config.validate_config()

# Schema is managed by Alembic (see backend/alembic/), not created here.
# Run `alembic upgrade head` before starting the app — see README.md
# ("Database migrations"). This applies to every environment (SQLite or
# PostgreSQL, local or deployed) so there is exactly one way schemas
# ever get created, never two mechanisms that can drift apart.


@asynccontextmanager
async def lifespan(app: FastAPI):
    db = SessionLocal()
    try:
        seed_if_empty(db)
    finally:
        db.close()

    yield


def _docs_kwargs(disable_docs: bool) -> dict:
    """FastAPI's docs_url/redoc_url/openapi_url constructor arguments —
    a plain function (rather than inline in the FastAPI(...) call below)
    so this gating logic can be unit-tested on its own, independent of
    constructing a whole app. Passing None to any of these three
    disables that specific endpoint entirely (see config.DISABLE_DOCS
    for why the default is "enabled")."""
    if disable_docs:
        return {"docs_url": None, "redoc_url": None, "openapi_url": None}
    return {"docs_url": "/docs", "redoc_url": "/redoc", "openapi_url": "/openapi.json"}


app = FastAPI(
    title="AI Restaurant Agent",
    description="Stage 1 MVP: Q&A chatbot answering only from restaurant data",
    version="0.1.0",
    lifespan=lifespan,
    **_docs_kwargs(config.DISABLE_DOCS),
)


app.add_middleware(
    CORSMiddleware,
    allow_origins=config.ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
    # X-Conversation-Token (Stage 3 Step 5) carries the opaque
    # conversation-resumption handle back to the browser. Without this,
    # a cross-origin frontend on an allowed origin would still receive
    # the header over the wire but JS couldn't read it via
    # response.headers.get(...) — allow_headers above governs which
    # *request* headers a client may send, which is a separate thing.
    # A single named header, never "*" — this can only ever apply to an
    # origin ALLOWED_ORIGINS already trusted, never widen that allowlist.
    expose_headers=["X-Conversation-Token"],
)


# Registered AFTER the global CORSMiddleware above so it becomes the
# OUTERMOST layer (Starlette/ASGI middleware runs in reverse registration
# order — the last one added wraps everything registered before it). This
# is what lets it fully own the CORS decision (including preflight) for
# every /widget/* request before the global CORSMiddleware ever sees it,
# while every other path still reaches that global middleware unchanged.
# See app/widget_cors.py's module docstring for the full rationale.
app.middleware("http")(widget_cors_middleware)


# Registered AFTER widget_cors_middleware, making it the new OUTERMOST
# layer (same reverse-registration-order reasoning as above) — see
# app/security_headers.py's own module docstring for why that matters
# (these headers must land even on a response widget_cors_middleware
# answers directly, without ever reaching a route handler) and for
# exactly which headers are added and why.
app.middleware("http")(security_headers_middleware)


app.include_router(chat.router)
app.include_router(admin.router)
app.include_router(bookings.router)
app.include_router(platform_admin.router)
app.include_router(conversations.router)
app.include_router(whatsapp.router)
app.include_router(widget.router)


# Serves the embeddable widget.js (Stage 4 Phase E) — deliberately
# mounted at /static/widget, NOT under /widget/*, so it can never be
# mistaken for a widget_key by widget_cors_middleware's path-prefix
# check above, and needs no CORS handling at all (a <script src> load
# isn't CORS-gated by browsers the way fetch()/XHR are). Kept separate
# from the admin dashboard and frontend/, neither of which the backend
# serves.
app.mount(
    "/static/widget",
    StaticFiles(directory=Path(__file__).resolve().parent / "static_widget"),
    name="widget-static",
)


@app.get("/health")
def health_check(db: Session = Depends(get_db)):
    """
    Confirms the API process is running AND the database is reachable
    (Security & Production Hardening Audit finding D6) — a bare process
    check previously returned "ok" even during a DB outage. Uses the
    same get_db/SessionLocal session infrastructure as every other
    route, not a second database-access mechanism, and the session is
    closed the same way (get_db's own finally block) regardless of
    outcome, so this can never leak a connection.

    `SELECT 1` is the cheapest possible round-trip — no table access,
    safe to run on every poll of Railway's frequent HEALTHCHECK. On
    failure, the full exception is logged server-side only; the
    response body never includes DATABASE_URL, a driver error message,
    or any other internal detail that could describe the database
    itself. A non-2xx status (503) is what makes Docker's own
    HEALTHCHECK (see the Dockerfile, which polls this exact endpoint
    with urllib and treats any non-2xx as failed) and Railway's
    equivalent correctly detect the outage instead of reporting healthy.
    """
    try:
        db.execute(text("SELECT 1"))
    except Exception:
        logger.exception("Health check failed: database not reachable")
        return JSONResponse(status_code=503, content={"status": "error"})

    return {"status": "ok"}