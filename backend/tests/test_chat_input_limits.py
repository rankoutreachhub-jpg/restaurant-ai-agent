"""
/chat must reject an oversized message, an oversized history entry, or
too many history turns with a 422 (before ever reaching Gemini) — this
caps how much a single request can cost against the paid Gemini API.
"""

from app import llm
from app.schemas import CHAT_HISTORY_MAX_TURNS, CHAT_MESSAGE_MAX_LENGTH


def test_message_over_max_length_is_rejected(client):
    response = client.post(
        "/chat",
        json={
            "message": "a" * (CHAT_MESSAGE_MAX_LENGTH + 1),
            "history": [],
            "restaurant_id": 1,
        },
    )
    assert response.status_code == 422


def test_message_at_max_length_is_accepted(client, monkeypatch):
    monkeypatch.setattr(llm, "generate_reply", lambda **kwargs: "stub reply")
    response = client.post(
        "/chat",
        json={
            "message": "a" * CHAT_MESSAGE_MAX_LENGTH,
            "history": [],
            "restaurant_id": 1,
        },
    )
    assert response.status_code == 200


def test_history_entry_over_max_length_is_rejected(client):
    response = client.post(
        "/chat",
        json={
            "message": "hi",
            "history": [{"role": "user", "content": "a" * (CHAT_MESSAGE_MAX_LENGTH + 1)}],
            "restaurant_id": 1,
        },
    )
    assert response.status_code == 422


def test_too_many_history_turns_is_rejected(client):
    history = [{"role": "user", "content": "hi"}] * (CHAT_HISTORY_MAX_TURNS + 1)
    response = client.post(
        "/chat",
        json={"message": "hi", "history": history, "restaurant_id": 1},
    )
    assert response.status_code == 422


def test_history_at_max_turns_is_accepted(client, monkeypatch):
    monkeypatch.setattr(llm, "generate_reply", lambda **kwargs: "stub reply")
    history = [{"role": "user", "content": "hi"}] * CHAT_HISTORY_MAX_TURNS
    response = client.post(
        "/chat",
        json={"message": "hi", "history": history, "restaurant_id": 1},
    )
    assert response.status_code == 200
