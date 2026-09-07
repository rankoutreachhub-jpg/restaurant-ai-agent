"""
app/conversations.get_or_create_whatsapp_conversation (Stage 3 Step 6B):
Meta's 24-hour customer-service-window rule. Tested by directly
manipulating a conversation's updated_at to a known point in the past
(UTC, matching the function's own datetime.utcnow() strategy) rather
than through real elapsed time, so the boundary cases are exact and
deterministic.
"""

from datetime import datetime, timedelta

from app import conversations, models

_PHONE = "+447911123456"


def _restaurant(db):
    return db.query(models.Restaurant).filter(models.Restaurant.id == 1).first()


def _set_updated_at(db, conversation, when):
    conversation.updated_at = when
    db.commit()
    db.refresh(conversation)


def test_first_contact_creates_a_new_conversation(client, db):
    restaurant = _restaurant(db)
    phone = "+447900000001"
    conversation, is_new = conversations.get_or_create_whatsapp_conversation(db, restaurant, phone)
    assert is_new is True
    assert conversation.channel == "whatsapp"
    assert conversation.external_id == phone


def test_second_message_within_window_resumes_the_same_conversation(client, db):
    restaurant = _restaurant(db)
    phone = "+447900000002"
    first, is_new = conversations.get_or_create_whatsapp_conversation(db, restaurant, phone)
    assert is_new is True

    second, is_new_2 = conversations.get_or_create_whatsapp_conversation(db, restaurant, phone)
    assert is_new_2 is False
    assert second.id == first.id


def test_message_just_inside_24_hours_resumes(client, db):
    """23 hours 59 minutes since last activity — clearly within the window."""
    restaurant = _restaurant(db)
    phone = "+447900000003"
    conversation, _ = conversations.get_or_create_whatsapp_conversation(db, restaurant, phone)
    _set_updated_at(db, conversation, datetime.utcnow() - timedelta(hours=23, minutes=59))

    resumed, is_new = conversations.get_or_create_whatsapp_conversation(db, restaurant, phone)
    assert is_new is False
    assert resumed.id == conversation.id


def test_message_just_outside_24_hours_starts_new_conversation(client, db):
    """24 hours 1 minute since last activity — clearly outside the window."""
    restaurant = _restaurant(db)
    phone = "+447900000004"
    conversation, _ = conversations.get_or_create_whatsapp_conversation(db, restaurant, phone)
    _set_updated_at(db, conversation, datetime.utcnow() - timedelta(hours=24, minutes=1))

    new_conversation, is_new = conversations.get_or_create_whatsapp_conversation(db, restaurant, phone)
    assert is_new is True
    assert new_conversation.id != conversation.id


def test_exactly_24_hours_boundary_is_inclusive(client, db, monkeypatch):
    """Exactly 24:00:00 since last activity — the documented, deliberate
    boundary choice (>=) treats this as still within the window. Time is
    frozen for the resolver's own "now" so the two 24-hours-apart
    timestamps are exactly, deterministically equal rather than racing
    against real elapsed execution time between two utcnow() calls."""
    restaurant = _restaurant(db)
    phone = "+447900000005"
    conversation, _ = conversations.get_or_create_whatsapp_conversation(db, restaurant, phone)

    frozen_now = datetime.utcnow()
    _set_updated_at(db, conversation, frozen_now - timedelta(hours=24))

    class _FrozenDateTime(datetime):
        @classmethod
        def utcnow(cls):
            return frozen_now

    monkeypatch.setattr(conversations, "datetime", _FrozenDateTime)

    resumed, is_new = conversations.get_or_create_whatsapp_conversation(db, restaurant, phone)
    assert is_new is False
    assert resumed.id == conversation.id


def test_one_second_past_24_hours_starts_new_conversation(client, db):
    """24:00:01 since last activity — one second past the inclusive
    boundary above — must start a new conversation."""
    restaurant = _restaurant(db)
    phone = "+447900000006"
    conversation, _ = conversations.get_or_create_whatsapp_conversation(db, restaurant, phone)
    _set_updated_at(db, conversation, datetime.utcnow() - timedelta(hours=24, seconds=1))

    new_conversation, is_new = conversations.get_or_create_whatsapp_conversation(db, restaurant, phone)
    assert is_new is True
    assert new_conversation.id != conversation.id


def test_different_customers_never_share_a_conversation(client, db):
    restaurant = _restaurant(db)
    conv_a, _ = conversations.get_or_create_whatsapp_conversation(db, restaurant, "+447900000007")
    conv_b, _ = conversations.get_or_create_whatsapp_conversation(db, restaurant, "+447900000008")
    assert conv_a.id != conv_b.id


def test_web_and_whatsapp_conversations_for_the_same_restaurant_never_collide(client, db):
    """A web /chat conversation (channel='web', external_id=None) must
    never be mistaken for a WhatsApp conversation, even for the same
    restaurant."""
    restaurant = _restaurant(db)
    web_conversation, _ = conversations.get_or_create_conversation(db, restaurant, None)
    assert web_conversation.channel == "web"
    assert web_conversation.external_id is None

    whatsapp_conversation, is_new = conversations.get_or_create_whatsapp_conversation(
        db, restaurant, "+447900000009"
    )
    assert is_new is True
    assert whatsapp_conversation.id != web_conversation.id


def test_latest_matching_conversation_is_resumed(client, db):
    """If somehow more than one conversation could match (shouldn't
    happen in normal operation, but the resolver explicitly orders by
    updated_at desc), the MOST RECENTLY active one is the one resumed."""
    restaurant = _restaurant(db)
    phone = "+447900000010"

    older = models.Conversation(
        restaurant_id=restaurant.id,
        public_token="older-token-testonly",
        channel="whatsapp",
        external_id=phone,
        updated_at=datetime.utcnow() - timedelta(hours=1),
    )
    newer = models.Conversation(
        restaurant_id=restaurant.id,
        public_token="newer-token-testonly",
        channel="whatsapp",
        external_id=phone,
        updated_at=datetime.utcnow() - timedelta(minutes=1),
    )
    db.add_all([older, newer])
    db.commit()

    resumed, is_new = conversations.get_or_create_whatsapp_conversation(db, restaurant, phone)
    assert is_new is False
    assert resumed.id == newer.id
