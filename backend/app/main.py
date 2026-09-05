"""
Main FastAPI application.

Run this with:
    uvicorn app.main:app --reload

from inside the backend/ folder.
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import models, config
from .database import engine, SessionLocal
from .routers import chat, admin
from .seed_data import seed_if_empty

# So errors logged with logger.exception() (see routers/chat.py) show up
# with a timestamp and level on the server console, instead of relying on
# Python's bare last-resort stderr handler.
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

config.validate_config()

models.Base.metadata.create_all(bind=engine)


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
)


app.include_router(chat.router)
app.include_router(admin.router)


@app.get("/health")
def health_check():
    """Simple endpoint to confirm the API is running."""
    return {"status": "ok"}