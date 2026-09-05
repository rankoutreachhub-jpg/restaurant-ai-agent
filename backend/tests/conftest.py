"""
Shared pytest fixtures.

Required environment variables are set here *before* the app package is
imported, since app/config.py reads them at import time. The database
and the log directory are each pointed at a fresh temporary location so
tests never touch a real backend/restaurant.db or backend/logs/, and
pre-existing env vars (a real .env file, or variables already set in
the shell) are left untouched via setdefault().
"""

import os
import tempfile

os.environ.setdefault("GEMINI_API_KEY", "AIzaSyTEST0000000000000000000000000")
os.environ.setdefault("ADMIN_API_KEY", "test-admin-key-for-pytest-only")

_db_fd, _db_path = tempfile.mkstemp(suffix=".db")
os.close(_db_fd)
os.environ["DATABASE_URL"] = f"sqlite:///{_db_path}"

os.environ["LOG_DIR"] = tempfile.mkdtemp(suffix="-logs")

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.rate_limit import admin_rate_limiter, chat_rate_limiter

ADMIN_API_KEY = os.environ["ADMIN_API_KEY"]


@pytest.fixture()
def client():
    """A TestClient with the app's startup (DB creation + seeding) run."""
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def admin_headers():
    return {"X-Admin-API-Key": ADMIN_API_KEY}


@pytest.fixture(autouse=True)
def _reset_rate_limits():
    """
    The rate limiters are module-level singletons shared by every test,
    and starlette's TestClient always reports the same fake client IP
    ("testclient"), so without this, hits from one test would count
    against the next one. Reset before every test for isolation.
    """
    chat_rate_limiter.reset()
    admin_rate_limiter.reset()
    yield
