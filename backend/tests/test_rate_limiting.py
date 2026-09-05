"""
/chat and /admin/* must each start returning 429 once a client exceeds
their per-IP request budget within the window, and recover once the
window has passed.
"""

from app import llm
from app.rate_limit import admin_rate_limiter, chat_rate_limiter


def test_chat_is_rate_limited_after_max_requests(client, monkeypatch):
    monkeypatch.setattr(llm, "generate_reply", lambda **kwargs: "stub reply")
    payload = {"message": "hi", "history": [], "restaurant_id": 1}

    for _ in range(chat_rate_limiter.max_requests):
        response = client.post("/chat", json=payload)
        assert response.status_code == 200

    response = client.post("/chat", json=payload)
    assert response.status_code == 429
    assert "Retry-After" in response.headers
    assert "detail" in response.json()


def test_admin_is_rate_limited_after_max_requests(client, admin_headers):
    for _ in range(admin_rate_limiter.max_requests):
        response = client.get("/admin/restaurant/1", headers=admin_headers)
        assert response.status_code == 200

    response = client.get("/admin/restaurant/1", headers=admin_headers)
    assert response.status_code == 429
    assert "Retry-After" in response.headers


def test_admin_rate_limit_applies_even_without_a_valid_key(client):
    """
    Rate limiting must run before the auth check, so brute-forcing the
    admin key is throttled too — not just successfully authenticated
    requests.
    """
    for _ in range(admin_rate_limiter.max_requests):
        response = client.get("/admin/restaurant/1", headers={"X-Admin-API-Key": "guess"})
        assert response.status_code == 401

    response = client.get("/admin/restaurant/1", headers={"X-Admin-API-Key": "guess"})
    assert response.status_code == 429


def test_health_is_never_rate_limited(client):
    for _ in range(admin_rate_limiter.max_requests + 5):
        response = client.get("/health")
        assert response.status_code == 200
