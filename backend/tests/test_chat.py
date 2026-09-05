"""
The /chat and /health endpoints must keep working without any admin
key — the admin-auth change must not affect customer-facing chat.
The Gemini call itself is stubbed out so these tests don't need a real
API key or network access.
"""

from app import llm


def test_chat_does_not_require_admin_key(client, monkeypatch):
    monkeypatch.setattr(llm, "generate_reply", lambda **kwargs: "Test reply from stub.")

    response = client.post(
        "/chat",
        json={"message": "What are your opening hours?", "history": [], "restaurant_id": 1},
    )

    assert response.status_code == 200
    assert response.json() == {"reply": "Test reply from stub."}


def test_health_does_not_require_admin_key(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
