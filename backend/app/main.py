"""
Main FastAPI application.

Run this with:
    uvicorn app.main:app --reload

from inside the backend/ folder.
"""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.staticfiles import StaticFiles

from . import config
from .database import SessionLocal
from .logging_config import configure_logging
from .monitoring import init_sentry
from .routers import chat, admin, bookings, platform_admin, conversations, whatsapp, widget
from .seed_data import seed_if_empty
from .widget_cors import widget_cors_middleware

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


app = FastAPI(
    title="AI Restaurant Agent",
    description="Stage 1 MVP: Q&A chatbot answering only from restaurant data",
    version="0.1.0",
    lifespan=lifespan,
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
def health_check():
    """Simple endpoint to confirm the API is running."""
    return {"status": "ok"}