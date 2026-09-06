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
from pathlib import Path

os.environ.setdefault("GEMINI_API_KEY", "AIzaSyTEST0000000000000000000000000")
os.environ.setdefault("ADMIN_API_KEY", "test-admin-key-for-pytest-only")

_db_fd, _db_path = tempfile.mkstemp(suffix=".db")
os.close(_db_fd)
os.environ["DATABASE_URL"] = f"sqlite:///{_db_path}"

os.environ["LOG_DIR"] = tempfile.mkdtemp(suffix="-logs")

# Build the test database's schema through Alembic — the same mechanism
# every other environment uses (see app/main.py) — rather than a
# separate create_all() path. This means the full test suite also
# verifies the migrations themselves apply cleanly, on every run.
from alembic import command
from alembic.config import Config

_alembic_ini = Path(__file__).resolve().parent.parent / "alembic.ini"
command.upgrade(Config(str(_alembic_ini)), "head")

import pytest
from fastapi.testclient import TestClient

from app.database import SessionLocal
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


@pytest.fixture()
def db(client):
    """
    A direct DB session for tests that call service-layer functions
    (e.g. app/booking.py) without going through the HTTP layer. Depends
    on `client` purely to ensure the schema exists and seed data has
    run first.
    """
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def second_restaurant(client, admin_headers):
    """
    A second, fully independent restaurant (id != 1) for cross-tenant
    authorization tests (Stage 3 Step 3). Created through the real
    /admin/platform/restaurants endpoint with the superadmin key, then
    given opening hours through the real POST .../opening-hours endpoint
    (Stage 3 Step 4) — no direct DB insert needed any more. Returns the
    new restaurant's id.
    """
    response = client.post(
        "/admin/platform/restaurants",
        json={
            "name": "The Anchor",
            "address": "1 Quay Street, Bristol, BS1 4EF",
            "phone": "0117 000 0000",
            "email": "hello@theanchor-bristol.co.uk",
            "seating_capacity": 30,
        },
        headers=admin_headers,
    )
    assert response.status_code == 201
    restaurant_id = response.json()["restaurant"]["id"]

    hours_response = client.post(
        f"/admin/restaurant/{restaurant_id}/opening-hours",
        json={
            "days": [
                {"day_of_week": day, "open_time": "12:00", "close_time": "22:00", "is_closed": False}
                for day in [
                    "Monday", "Tuesday", "Wednesday", "Thursday",
                    "Friday", "Saturday", "Sunday",
                ]
            ]
        },
        headers=admin_headers,
    )
    assert hours_response.status_code == 201

    return restaurant_id


@pytest.fixture()
def scoped_admin_key(client, admin_headers):
    """
    Factory fixture: scoped_admin_key(restaurant_ids) creates a real
    restaurant-scoped admin user via /admin/platform/admin-users (using
    the superadmin key) and returns (admin_user_id, headers) using the
    key actually issued by the API — not a hand-constructed one.
    """
    def _make(restaurant_ids, label="test scoped admin"):
        response = client.post(
            "/admin/platform/admin-users",
            json={"label": label, "restaurant_ids": restaurant_ids},
            headers=admin_headers,
        )
        assert response.status_code == 201
        body = response.json()
        return body["id"], {"X-Admin-API-Key": body["api_key"]}

    return _make


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
