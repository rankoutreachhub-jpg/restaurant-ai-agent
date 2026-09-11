"""
Conversation/message persistence (Stage 3 Step 5). Only the Gemini
network call is stubbed, mirroring tests/test_chat_booking_tool.py's
pattern — everything else (conversation resolution, history loading,
persistence) is real.

Date allocation: this file's booking-tool-triggering tests use their own
anchor, offset from every other file's range (see
test_booking_service.py's module docstring for the overall convention).
"""

import itertools
from datetime import date, timedelta
from types import SimpleNamespace

from app import models
from app import llm as llm_module
from app.database import SessionLocal

_ANCHOR = date.today() + timedelta(days=300)
_counter = itertools.count(1)


def _fresh_date() -> date:
    return _ANCHOR + timedelta(weeks=next(_counter))


_SAFE_TIME = "13:00"


def _fake_response(text=None, function_calls=None):
    return SimpleNamespace(
        text=text,
        function_calls=function_calls or [],
        candidates=[SimpleNamespace(content=SimpleNamespace(role="model", parts=[]))],
    )


def _fake_call(args):
    return SimpleNamespace(name="create_booking", args=args)


def _stub_plain_reply(monkeypatch, text, capture=None):
    def fake_generate_content(model, contents, config):
        if capture is not None:
            capture.append(contents)
        return _fake_response(text=text)

    monkeypatch.setattr(llm_module.client.models, "generate_content", fake_generate_content)


def _stub_tool_round_trip(monkeypatch, call_args, final_text):
    calls = {"n": 0}

    def fake_generate_content(model, contents, config):
        calls["n"] += 1
        if calls["n"] == 1:
            return _fake_response(function_calls=[_fake_call(call_args)])
        return _fake_response(text=final_text)

    monkeypatch.setattr(llm_module.client.models, "generate_content", fake_generate_content)


def _conversations_for(db, restaurant_id=1):
    return db.query(models.Conversation).filter(models.Conversation.restaurant_id == restaurant_id).all()


def test_new_conversation_created_and_token_returned(client, monkeypatch, db):
    _stub_plain_reply(monkeypatch, "Hiya! How can I help?")
    before = len(_conversations_for(db))

    response = client.post("/chat", json={"message": "hi", "history": [], "restaurant_id": 1})

    assert response.status_code == 200
    token = response.headers.get("X-Conversation-Token")
    assert token
    assert len(_conversations_for(db)) == before + 1

    conversation = db.query(models.Conversation).filter(models.Conversation.public_token == token).one()
    messages = (
        db.query(models.Message)
        .filter(models.Message.conversation_id == conversation.id)
        .order_by(models.Message.created_at)
        .all()
    )
    assert [m.role for m in messages] == ["user", "assistant"]
    assert messages[0].content == "hi"
    assert messages[1].content == "Hiya! How can I help?"


def test_no_message_ever_has_a_system_role(client, monkeypatch, db):
    _stub_plain_reply(monkeypatch, "Sure thing!")
    client.post("/chat", json={"message": "What's on the menu?", "history": [], "restaurant_id": 1})

    roles = {row[0] for row in db.query(models.Message.role).all()}
    assert roles <= {"user", "assistant"}
    assert "system" not in roles


def test_resumption_uses_persisted_history_not_client_supplied_history(client, monkeypatch, db):
    _stub_plain_reply(monkeypatch, "Nice to meet you!")
    first = client.post(
        "/chat", json={"message": "My favourite colour is chartreuse", "history": [], "restaurant_id": 1}
    )
    token = first.headers["X-Conversation-Token"]

    captured = []
    _stub_plain_reply(monkeypatch, "Got it!", capture=captured)
    second = client.post(
        "/chat",
        json={
            "message": "What did I just tell you?",
            "history": [{"role": "user", "content": "TOTALLY UNRELATED FABRICATED HISTORY"}],
            "restaurant_id": 1,
            "conversation_token": token,
        },
    )
    assert second.status_code == 200

    sent_contents = captured[0]
    sent_texts = [part.text for turn in sent_contents for part in turn.parts]
    assert any("chartreuse" in t for t in sent_texts)
    assert not any("FABRICATED" in t for t in sent_texts)


def test_unknown_token_silently_starts_a_new_conversation(client, monkeypatch, db):
    _stub_plain_reply(monkeypatch, "Hiya!")
    response = client.post(
        "/chat",
        json={"message": "hi", "history": [], "restaurant_id": 1, "conversation_token": "not-a-real-token"},
    )
    assert response.status_code == 200
    new_token = response.headers["X-Conversation-Token"]
    assert new_token != "not-a-real-token"


def test_token_from_another_restaurant_does_not_resume_or_leak(client, monkeypatch, db, second_restaurant):
    _stub_plain_reply(monkeypatch, "Reply for restaurant 1")
    first = client.post(
        "/chat", json={"message": "Secret restaurant-1 detail", "history": [], "restaurant_id": 1}
    )
    token_for_restaurant_1 = first.headers["X-Conversation-Token"]

    captured = []
    _stub_plain_reply(monkeypatch, "Reply for restaurant 2", capture=captured)
    second = client.post(
        "/chat",
        json={
            "message": "hi",
            "history": [],
            "restaurant_id": second_restaurant,
            "conversation_token": token_for_restaurant_1,
        },
    )
    assert second.status_code == 200
    new_token = second.headers["X-Conversation-Token"]
    assert new_token != token_for_restaurant_1

    # No restaurant-1 content ever reached the restaurant-2 conversation.
    sent_texts = [part.text for turn in captured[0] for part in turn.parts]
    assert not any("Secret restaurant-1 detail" in t for t in sent_texts)

    conv1 = db.query(models.Conversation).filter(models.Conversation.public_token == token_for_restaurant_1).one()
    assert conv1.restaurant_id == 1
    conv2 = db.query(models.Conversation).filter(models.Conversation.public_token == new_token).one()
    assert conv2.restaurant_id == second_restaurant
    assert conv1.id != conv2.id


def test_resumed_history_is_capped_at_max_turns(client, monkeypatch, db):
    _stub_plain_reply(monkeypatch, "start")
    first = client.post("/chat", json={"message": "start", "history": [], "restaurant_id": 1})
    token = first.headers["X-Conversation-Token"]
    conversation = db.query(models.Conversation).filter(models.Conversation.public_token == token).one()

    # Seed far more turns than the cap allows.
    from datetime import datetime
    for i in range(60):
        db.add(models.Message(conversation_id=conversation.id, role="user", content=f"turn {i}"))
        db.add(models.Message(conversation_id=conversation.id, role="assistant", content=f"reply {i}"))
    db.commit()

    captured = []
    _stub_plain_reply(monkeypatch, "final", capture=captured)
    response = client.post(
        "/chat",
        json={"message": "one more", "history": [], "restaurant_id": 1, "conversation_token": token},
    )
    assert response.status_code == 200

    from app.schemas import CHAT_HISTORY_MAX_TURNS
    sent_contents = captured[0]
    # sent_contents = capped history + the new user turn.
    assert len(sent_contents) == CHAT_HISTORY_MAX_TURNS + 1


def test_triggered_tool_call_is_recorded_on_successful_booking(client, monkeypatch, db):
    d = _fresh_date()
    args = {
        "customer_name": "Persistence Tester",
        "phone": "01234 000111",
        "email": "persistence-tester@example.com",
        "booking_date": d.isoformat(),
        "booking_time": _SAFE_TIME,
        "party_size": 2,
    }
    _stub_tool_round_trip(monkeypatch, args, "You're booked in!")

    response = client.post("/chat", json={"message": "Book me a table", "history": [], "restaurant_id": 1})
    assert response.status_code == 200
    token = response.headers["X-Conversation-Token"]

    conversation = db.query(models.Conversation).filter(models.Conversation.public_token == token).one()
    assistant_message = (
        db.query(models.Message)
        .filter(models.Message.conversation_id == conversation.id, models.Message.role == "assistant")
        .one()
    )
    assert assistant_message.triggered_tool_call is True


def test_plain_chat_does_not_set_triggered_tool_call(client, monkeypatch, db):
    _stub_plain_reply(monkeypatch, "Just chatting")
    response = client.post("/chat", json={"message": "hi", "history": [], "restaurant_id": 1})
    token = response.headers["X-Conversation-Token"]

    conversation = db.query(models.Conversation).filter(models.Conversation.public_token == token).one()
    assistant_message = (
        db.query(models.Message)
        .filter(models.Message.conversation_id == conversation.id, models.Message.role == "assistant")
        .one()
    )
    assert assistant_message.triggered_tool_call is False


def test_booking_succeeds_but_final_reply_failure_does_not_persist_the_turn(client, monkeypatch, db):
    """
    Traced edge case: create_booking()'s commit happens inside the tool
    handler, BEFORE the second (wrap-up) Gemini call. If that second
    call then raises, the booking is already durably committed even
    though the customer receives a generic 500. The existing booking
    architecture has no compensating-transaction mechanism to undo it,
    and none is added here — that would be exactly the "large
    transaction redesign" explicitly out of scope. What persistence
    MUST get right: this failed turn is never falsely recorded as saved,
    and the conversation's updated_at never advances for it.
    """
    d = _fresh_date()
    call_count = {"n": 0}

    def fake_generate_content(model, contents, config):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return _fake_response(function_calls=[_fake_call({
                "customer_name": "Edge Case Customer",
                "phone": "01234 999888",
                "email": "edge-case@example.com",
                "booking_date": d.isoformat(),
                "booking_time": _SAFE_TIME,
                "party_size": 2,
            })])
        raise RuntimeError("simulated upstream failure on the second Gemini call")

    monkeypatch.setattr(llm_module.client.models, "generate_content", fake_generate_content)

    # Establish a real conversation first so we have a baseline updated_at.
    _stub_plain_reply(monkeypatch, "hello")
    setup = client.post("/chat", json={"message": "hi", "history": [], "restaurant_id": 1})
    token = setup.headers["X-Conversation-Token"]
    conversation_id = (
        db.query(models.Conversation).filter(models.Conversation.public_token == token).one().id
    )
    db.expire_all()
    updated_at_before = (
        db.query(models.Conversation.updated_at).filter(models.Conversation.id == conversation_id).scalar()
    )
    messages_before = (
        db.query(models.Message).filter(models.Message.conversation_id == conversation_id).count()
    )

    monkeypatch.setattr(llm_module.client.models, "generate_content", fake_generate_content)
    response = client.post(
        "/chat",
        json={"message": "Book me a table", "history": [], "restaurant_id": 1, "conversation_token": token},
    )

    assert response.status_code == 500

    # The booking is real and NOT rolled back — nothing in this stage
    # attempts to undo it.
    booking = (
        db.query(models.Booking)
        .filter(models.Booking.restaurant_id == 1, models.Booking.booking_date == d)
        .one()
    )
    assert booking.customer_name == "Edge Case Customer"

    # But the failed turn was never persisted, and updated_at is untouched.
    db.expire_all()
    messages_after = (
        db.query(models.Message).filter(models.Message.conversation_id == conversation_id).count()
    )
    assert messages_after == messages_before
    updated_at_after = (
        db.query(models.Conversation.updated_at).filter(models.Conversation.id == conversation_id).scalar()
    )
    assert updated_at_after == updated_at_before


def test_new_conversation_gemini_failure_creates_no_orphaned_conversation(client, monkeypatch, db):
    """
    Regression test for the reported production bug: a conversation
    visible in the admin dashboard whose messages endpoint returns [].
    Root cause was get_or_create_conversation() committing a brand-new
    conversation immediately, before the Gemini call that could fail.
    It is now only flush()ed (not committed) until persist_turn()'s own
    commit, so a Gemini failure on a conversation's very first turn must
    leave no trace of that conversation at all.
    """
    def _boom(model, contents, config):
        raise RuntimeError("simulated Gemini outage")

    monkeypatch.setattr(llm_module.client.models, "generate_content", _boom)
    before = len(_conversations_for(db))

    response = client.post("/chat", json={"message": "hi", "history": [], "restaurant_id": 1})
    assert response.status_code == 500

    db.expire_all()
    assert len(_conversations_for(db)) == before


def test_existing_conversation_plain_gemini_failure_leaves_conversation_intact(client, monkeypatch, db):
    """
    A conversation that already exists (a prior turn succeeded) must
    survive a LATER Gemini failure untouched: still present, its
    updated_at unmoved, and no partial message added for the failed
    turn -- distinct from
    test_new_conversation_gemini_failure_creates_no_orphaned_conversation
    above, which covers a conversation's very first turn failing.
    """
    _stub_plain_reply(monkeypatch, "hello")
    setup = client.post("/chat", json={"message": "hi", "history": [], "restaurant_id": 1})
    token = setup.headers["X-Conversation-Token"]
    conversation_id = (
        db.query(models.Conversation).filter(models.Conversation.public_token == token).one().id
    )

    db.expire_all()
    updated_at_before = (
        db.query(models.Conversation.updated_at).filter(models.Conversation.id == conversation_id).scalar()
    )
    messages_before = (
        db.query(models.Message).filter(models.Message.conversation_id == conversation_id).count()
    )

    def _boom(model, contents, config):
        raise RuntimeError("simulated Gemini outage on a later turn")

    monkeypatch.setattr(llm_module.client.models, "generate_content", _boom)
    response = client.post(
        "/chat",
        json={"message": "still there?", "history": [], "restaurant_id": 1, "conversation_token": token},
    )
    assert response.status_code == 500

    db.expire_all()
    still_exists = db.query(models.Conversation).filter(models.Conversation.id == conversation_id).one()
    assert still_exists.updated_at == updated_at_before
    messages_after = (
        db.query(models.Message).filter(models.Message.conversation_id == conversation_id).count()
    )
    assert messages_after == messages_before
