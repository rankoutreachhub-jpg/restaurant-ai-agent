"""
Shared pytest fixtures.

Required environment variables are set here *before* the app package is
imported, since app/config.py reads them at import time. The database is
pointed at a fresh temporary file so tests never touch a real
backend/restaurant.db, and pre-existing env vars (a real .env file, or
variables already set in the shell) are left untouched via setdefault().
"""

import os
import tempfile

os.environ.setdefault("GEMINI_API_KEY", "AIzaSyTEST0000000000000000000000000")
os.environ.setdefault("ADMIN_API_KEY", "test-admin-key-for-pytest-only")

_db_fd, _db_path = tempfile.mkstemp(suffix=".db")
os.close(_db_fd)
os.environ["DATABASE_URL"] = f"sqlite:///{_db_path}"

import pytest
from fastapi.testclient import TestClient

from app.main import app

ADMIN_API_KEY = os.environ["ADMIN_API_KEY"]


@pytest.fixture()
def client():
    """A TestClient with the app's startup (DB creation + seeding) run."""
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def admin_headers():
    return {"X-Admin-API-Key": ADMIN_API_KEY}
