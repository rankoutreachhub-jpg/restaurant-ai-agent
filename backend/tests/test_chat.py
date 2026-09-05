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


def test_chat_error_does_not_leak_internal_exception_details(client, monkeypatch, caplog):
    sensitive_detail = "API key not valid: sk-super-secret-upstream-key-12345"

    def _boom(**kwargs):
        raise RuntimeError(sensitive_detail)

    monkeypatch.setattr(llm, "generate_reply", _boom)

    with caplog.at_level("ERROR"):
        response = client.post(
            "/chat",
            json={"message": "hi", "history": [], "restaurant_id": 1},
        )

    assert response.status_code == 500
    body = response.json()
    assert sensitive_detail not in body["detail"]
    assert "RuntimeError" not in body["detail"]
    assert body["detail"] == (
        "Sorry, something went wrong on our end. Please try again shortly."
    )

    # The real detail should still be captured server-side (e.g. for an
    # operator watching logs), just never sent back to the client.
    assert sensitive_detail in caplog.text
