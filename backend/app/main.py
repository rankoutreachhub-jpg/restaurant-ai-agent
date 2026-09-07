"""
Main FastAPI application.

Run this with:
    uvicorn app.main:app --reload

from inside the backend/ folder.
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import config
from .database import SessionLocal
from .logging_config import configure_logging
from .routers import chat, admin, bookings, platform_admin, conversations, whatsapp, widget
from .seed_data import seed_if_empty

# Console + rotating log file for errors and important events (see
# app/logging_config.py) — set up before anything else logs, so nothing
# is missed.
configure_logging()

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


app.include_router(chat.router)
app.include_router(admin.router)
app.include_router(bookings.router)
app.include_router(platform_admin.router)
app.include_router(conversations.router)
app.include_router(whatsapp.router)
app.include_router(widget.router)


@app.get("/health")
def health_check():
    """Simple endpoint to confirm the API is running."""
    return {"status": "ok"}