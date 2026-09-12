"""
GET /health (Security & Production Hardening Audit finding D6). Previously
a bare process check that returned "ok" even during a database outage --
see app/main.py's health_check() docstring for the full rationale.
"""

from app.database import get_db
from app.main import app as fastapi_app


def test_health_returns_ok_when_the_database_is_reachable(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_health_returns_a_non_200_when_the_database_check_fails(client):
    class _BrokenSession:
        def execute(self, *args, **kwargs):
            raise RuntimeError(
                "connection to server at internal-db-host.example failed: "
                "password authentication failed for user 'realdbuser'"
            )

        def close(self):
            pass

    def _broken_get_db():
        db = _BrokenSession()
        try:
            yield db
        finally:
            db.close()

    fastapi_app.dependency_overrides[get_db] = _broken_get_db
    try:
        response = client.get("/health")
    finally:
        del fastapi_app.dependency_overrides[get_db]

    assert response.status_code == 503
    assert response.json() == {"status": "error"}


def test_health_failure_response_never_exposes_internal_database_details(client):
    class _BrokenSession:
        def execute(self, *args, **kwargs):
            raise RuntimeError(
                "connection to server at internal-db-host.example failed: "
                "password authentication failed for user 'realdbuser'"
            )

        def close(self):
            pass

    def _broken_get_db():
        db = _BrokenSession()
        try:
            yield db
        finally:
            db.close()

    fastapi_app.dependency_overrides[get_db] = _broken_get_db
    try:
        response = client.get("/health")
    finally:
        del fastapi_app.dependency_overrides[get_db]

    body_text = response.text
    assert "internal-db-host" not in body_text
    assert "realdbuser" not in body_text
    assert "password" not in body_text
    assert "Traceback" not in body_text


def test_health_check_does_not_leak_a_broken_override_into_later_requests(client):
    """Regression guard for this test file's own technique: overriding
    get_db must be fully cleaned up, or every OTHER route using
    Depends(get_db) would start failing too."""
    class _BrokenSession:
        def execute(self, *args, **kwargs):
            raise RuntimeError("simulated outage")

        def close(self):
            pass

    def _broken_get_db():
        db = _BrokenSession()
        try:
            yield db
        finally:
            db.close()

    fastapi_app.dependency_overrides[get_db] = _broken_get_db
    try:
        assert client.get("/health").status_code == 503
    finally:
        del fastapi_app.dependency_overrides[get_db]

    assert client.get("/health").status_code == 200
