"""
Read-only admin conversation/message endpoints (Stage 3 Step 5).
Same auth/rate-limit/scoping protection as every other admin resource —
get_current_admin + require_restaurant_access, reused verbatim.
"""

from types import SimpleNamespace


def _stub_reply(monkeypatch, text="Hello!"):
    from app import llm

    def fake_generate_content(model, contents, config):
        return SimpleNamespace(
            text=text,
            function_calls=[],
            candidates=[SimpleNamespace(content=SimpleNamespace(role="model", parts=[]))],
        )

    monkeypatch.setattr(llm.client.models, "generate_content", fake_generate_content)


def _create_conversation(client, monkeypatch, message="hi", restaurant_id=1):
    _stub_reply(monkeypatch, f"reply to: {message}")
    response = client.post(
        "/chat", json={"message": message, "history": [], "restaurant_id": restaurant_id}
    )
    assert response.status_code == 200
    return response.headers["X-Conversation-Token"]


def test_list_conversations_requires_admin_key(client):
    response = client.get("/admin/restaurant/1/conversations")
    assert response.status_code == 401


def test_list_conversations_with_admin_key_succeeds_and_hides_public_token(client, monkeypatch, admin_headers):
    _create_conversation(client, monkeypatch, message="marker-conversation-1")

    response = client.get("/admin/restaurant/1/conversations", headers=admin_headers)
    assert response.status_code == 200
    body = response.json()
    assert isinstance(body, list)
    assert len(body) >= 1
    for entry in body:
        assert "public_token" not in entry
        assert set(entry.keys()) == {"id", "restaurant_id", "channel", "created_at", "updated_at"}


def test_list_conversations_ordered_most_recent_first(client, monkeypatch, admin_headers):
    _create_conversation(client, monkeypatch, message="first")
    _create_conversation(client, monkeypatch, message="second")
    _create_conversation(client, monkeypatch, message="third")

    body = client.get("/admin/restaurant/1/conversations", headers=admin_headers).json()
    updated_ats = [c["updated_at"] for c in body]
    assert updated_ats == sorted(updated_ats, reverse=True)


def test_list_conversations_pagination(client, monkeypatch, admin_headers):
    for i in range(5):
        _create_conversation(client, monkeypatch, message=f"pagination-{i}")

    page1 = client.get("/admin/restaurant/1/conversations?limit=2&offset=0", headers=admin_headers).json()
    page2 = client.get("/admin/restaurant/1/conversations?limit=2&offset=2", headers=admin_headers).json()
    assert len(page1) == 2
    assert len(page2) == 2
    assert {c["id"] for c in page1}.isdisjoint({c["id"] for c in page2})


def test_list_conversations_limit_is_bounded(client, admin_headers):
    response = client.get("/admin/restaurant/1/conversations?limit=99999", headers=admin_headers)
    assert response.status_code == 422


def test_list_conversations_404_for_unknown_restaurant(client, admin_headers):
    response = client.get("/admin/restaurant/999999/conversations", headers=admin_headers)
    assert response.status_code == 404


def test_list_conversation_messages_requires_admin_key(client, monkeypatch, admin_headers):
    token = _create_conversation(client, monkeypatch)
    from app import models
    from app.database import SessionLocal
    session = SessionLocal()
    try:
        conversation_id = (
            session.query(models.Conversation).filter(models.Conversation.public_token == token).one().id
        )
    finally:
        session.close()

    response = client.get(f"/admin/restaurant/1/conversations/{conversation_id}/messages")
    assert response.status_code == 401


def test_list_conversation_messages_succeeds_ordered_oldest_first(client, monkeypatch, admin_headers):
    token = _create_conversation(client, monkeypatch, message="ordering-check")
    from app import models
    from app.database import SessionLocal
    session = SessionLocal()
    try:
        conversation_id = (
            session.query(models.Conversation).filter(models.Conversation.public_token == token).one().id
        )
    finally:
        session.close()

    response = client.get(
        f"/admin/restaurant/1/conversations/{conversation_id}/messages", headers=admin_headers
    )
    assert response.status_code == 200
    body = response.json()
    assert [m["role"] for m in body] == ["user", "assistant"]
    assert body[0]["content"] == "ordering-check"
    created_ats = [m["created_at"] for m in body]
    assert created_ats == sorted(created_ats)
    for entry in body:
        assert set(entry.keys()) == {"id", "conversation_id", "role", "content", "triggered_tool_call", "created_at"}


def test_list_conversation_messages_404_for_unknown_conversation(client, admin_headers):
    response = client.get("/admin/restaurant/1/conversations/999999/messages", headers=admin_headers)
    assert response.status_code == 404


def test_conversation_admin_endpoints_cross_tenant_isolation(
    client, monkeypatch, second_restaurant, scoped_admin_key, admin_headers
):
    token_a = _create_conversation(client, monkeypatch, message="restaurant-1-only", restaurant_id=1)
    token_b = _create_conversation(client, monkeypatch, message="restaurant-2-only", restaurant_id=second_restaurant)

    from app import models
    from app.database import SessionLocal
    session = SessionLocal()
    try:
        conv_b_id = session.query(models.Conversation).filter(models.Conversation.public_token == token_b).one().id
    finally:
        session.close()

    _, headers = scoped_admin_key([1])  # scoped ONLY to restaurant 1

    # Can't list restaurant 2's conversations at all.
    assert client.get(
        f"/admin/restaurant/{second_restaurant}/conversations", headers=headers
    ).status_code == 404

    # Can't read restaurant 2's conversation's messages either.
    assert client.get(
        f"/admin/restaurant/{second_restaurant}/conversations/{conv_b_id}/messages", headers=headers
    ).status_code == 404

    # Superadmin still sees both.
    assert client.get("/admin/restaurant/1/conversations", headers=admin_headers).status_code == 200
    assert client.get(
        f"/admin/restaurant/{second_restaurant}/conversations", headers=admin_headers
    ).status_code == 200
