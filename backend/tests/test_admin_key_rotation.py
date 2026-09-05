"""
ADMIN_API_KEY_PREVIOUS lets an old admin key keep working temporarily
alongside a new ADMIN_API_KEY, so rotating the secret doesn't lock out
clients that haven't switched over yet. Covers: primary key, previous
key, an invalid key, and a missing key — with and without a rotation
in progress.
"""

import pytest

from app import config

OLD_KEY = "the-old-admin-key-being-rotated-out-of-service"


@pytest.fixture()
def with_previous_key(monkeypatch):
    """Simulates a rotation in progress: an old key is still honoured."""
    monkeypatch.setattr(config, "ADMIN_API_KEY_PREVIOUS", OLD_KEY)
    yield OLD_KEY


def test_primary_key_is_accepted(client, admin_headers):
    response = client.get("/admin/restaurant/1", headers=admin_headers)
    assert response.status_code == 200


def test_primary_key_still_works_during_a_rotation(client, admin_headers, with_previous_key):
    response = client.get("/admin/restaurant/1", headers=admin_headers)
    assert response.status_code == 200


def test_previous_key_is_accepted_during_rotation(client, with_previous_key):
    response = client.get(
        "/admin/restaurant/1",
        headers={"X-Admin-API-Key": with_previous_key},
    )
    assert response.status_code == 200


def test_previous_key_is_rejected_when_no_rotation_is_configured(client):
    # ADMIN_API_KEY_PREVIOUS is unset by default — a key that was never
    # configured as "previous" must not be accepted just because it looks
    # like an old key.
    response = client.get(
        "/admin/restaurant/1",
        headers={"X-Admin-API-Key": OLD_KEY},
    )
    assert response.status_code == 401


def test_invalid_key_is_rejected_during_rotation(client, with_previous_key):
    response = client.get(
        "/admin/restaurant/1",
        headers={"X-Admin-API-Key": "some-other-garbage-key"},
    )
    assert response.status_code == 401


def test_missing_key_is_rejected_during_rotation(client, with_previous_key):
    response = client.get("/admin/restaurant/1")
    assert response.status_code == 401


def test_admin_key_never_appears_in_401_response_body(client, with_previous_key):
    response = client.get(
        "/admin/restaurant/1",
        headers={"X-Admin-API-Key": "wrong-key"},
    )
    assert response.status_code == 401
    body_text = response.text
    assert config.ADMIN_API_KEY not in body_text
    assert OLD_KEY not in body_text
