"""
Jantar SaaS Phase 3 (see app/subscription_enforcement.py): actual
enforcement of the plan catalog (app/plans.py) that Phase 1/2 only ever
exposed for visibility. Covers all four gated surfaces — /chat,
/widget/{key}/chat, admin-user creation/grants, and WhatsApp mapping +
inbound processing — plus SubscriptionEvent writes and the month
boundary the underlying usage counter already relies on.

Conversation-quota tests bulk-insert Conversation rows directly (the
same pattern tests/test_subscription_visibility.py already uses for
usage-counter tests) rather than sending hundreds/thousands of real
HTTP requests — both because it's far faster and because
chat_rate_limiter/widget_chat_rate_limiter (10 requests/minute) would
otherwise trip long before any plan's conversation cap did. Only the
one or two requests actually asserting on the enforcement boundary go
through the real HTTP endpoint.

Every test below uses second_restaurant (or a freshly-created
restaurant), never restaurant 1 (The Kings Arms) — restaurant 1 is
mutated by other test files in this shared session (see
tests/conftest.py:whatsapp_number) and accumulates state across the
whole suite, so asserting specific plan/usage values on it here would
be order-dependent. test_kings_arms_restaurant_1_is_not_broken_by_enforcement
at the bottom of this file instead does a plan-agnostic sanity check
against whatever restaurant 1's actual current state is.
"""

import itertools
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from app import models
from app import llm as llm_module
from app import whatsapp_client
from app.whatsapp_processing import process_incoming_message


# --- Shared helpers -----------------------------------------------------

def _fake_response(text="Hiya!"):
    return SimpleNamespace(text=text, function_calls=[])


def _stub_plain_reply(monkeypatch, text="Hiya!"):
    def fake_generate_content(model, contents, config):
        return _fake_response(text)

    monkeypatch.setattr(llm_module.client.models, "generate_content", fake_generate_content)


def _set_plan(client, admin_headers, restaurant_id, plan_code=None, status=None):
    payload = {}
    if plan_code is not None:
        payload["plan_code"] = plan_code
    if status is not None:
        payload["status"] = status
    response = client.patch(
        f"/admin/platform/restaurants/{restaurant_id}/subscription",
        json=payload,
        headers=admin_headers,
    )
    assert response.status_code == 200
    return response.json()


_token_counter = itertools.count(1)


def _bulk_insert_conversations(db, restaurant_id, count, *, channel="web", when=None):
    # A module-wide counter, not one reset per call, since a single test
    # may call this more than once for the same restaurant_id/channel
    # (e.g. to simulate usage spanning two different months) — reusing
    # indices from 0 each call would collide on Conversation.public_token's
    # unique constraint.
    when = when or datetime.utcnow()
    db.bulk_save_objects([
        models.Conversation(
            restaurant_id=restaurant_id,
            public_token=f"quota-fill-{next(_token_counter)}",
            channel=channel,
            created_at=when,
            updated_at=when,
        )
        for _ in range(count)
    ])
    db.commit()


def _events_for(db, restaurant_id, event_type):
    return (
        db.query(models.SubscriptionEvent)
        .filter(
            models.SubscriptionEvent.restaurant_id == restaurant_id,
            models.SubscriptionEvent.event_type == event_type,
        )
        .all()
    )


def _create_admin_user(client, admin_headers, restaurant_ids, label="quota test admin"):
    return client.post(
        "/admin/platform/admin-users",
        json={"label": label, "restaurant_ids": restaurant_ids},
        headers=admin_headers,
    )


# =========================================================
# Conversation quota — /chat
# =========================================================

@pytest.mark.parametrize(
    "plan_code, limit",
    [("starter", 300), ("growth", 1000), ("pro", 3000)],
)
def test_new_conversation_allowed_up_to_the_plan_limit_then_blocked(
    client, monkeypatch, admin_headers, second_restaurant, db, plan_code, limit
):
    _set_plan(client, admin_headers, second_restaurant, plan_code=plan_code)
    _stub_plain_reply(monkeypatch)
    _bulk_insert_conversations(db, second_restaurant, limit - 1)

    # The limit-th new conversation this month is still allowed.
    at_limit = client.post(
        "/chat", json={"message": "hi", "restaurant_id": second_restaurant}
    )
    assert at_limit.status_code == 200

    # The (limit + 1)-th is blocked with a distinct 4xx, not a 500.
    over_limit = client.post(
        "/chat", json={"message": "hi again", "restaurant_id": second_restaurant}
    )
    assert over_limit.status_code == 402
    body = over_limit.json()["detail"]
    assert body["error_code"] == "conversation_quota_exceeded"
    assert str(limit) in body["message"]


def test_blocked_new_conversation_leaves_no_orphaned_conversation_row(
    client, monkeypatch, admin_headers, second_restaurant, db
):
    """A blocked attempt must not leave a zero-message Conversation
    behind — see subscription_enforcement.check_conversation_quota's
    docstring on why it rolls back the pending flush before recording
    the block."""
    _set_plan(client, admin_headers, second_restaurant, plan_code="starter")
    _stub_plain_reply(monkeypatch)
    _bulk_insert_conversations(db, second_restaurant, 300)

    before = db.query(models.Conversation).filter(
        models.Conversation.restaurant_id == second_restaurant
    ).count()

    response = client.post("/chat", json={"message": "hi", "restaurant_id": second_restaurant})
    assert response.status_code == 402

    db.expire_all()
    after = db.query(models.Conversation).filter(
        models.Conversation.restaurant_id == second_restaurant
    ).count()
    assert after == before


def test_resumed_conversation_continues_after_quota_reached(
    client, monkeypatch, admin_headers, second_restaurant, db
):
    _set_plan(client, admin_headers, second_restaurant, plan_code="starter")
    _stub_plain_reply(monkeypatch)

    first = client.post("/chat", json={"message": "hi", "restaurant_id": second_restaurant})
    assert first.status_code == 200
    token = first.headers["X-Conversation-Token"]

    # Push usage up to the limit with other conversations.
    _bulk_insert_conversations(db, second_restaurant, 299)

    resumed = client.post(
        "/chat",
        json={"message": "still here?", "restaurant_id": second_restaurant, "conversation_token": token},
    )
    assert resumed.status_code == 200

    brand_new = client.post("/chat", json={"message": "hi", "restaurant_id": second_restaurant})
    assert brand_new.status_code == 402


def test_widget_chat_enforces_the_same_conversation_quota(
    client, monkeypatch, admin_headers, second_restaurant, db
):
    config_response = client.post(
        f"/admin/restaurant/{second_restaurant}/widget-config",
        json={"is_active": True},
        headers=admin_headers,
    )
    assert config_response.status_code == 201
    widget_key = config_response.json()["widget_key"]

    _set_plan(client, admin_headers, second_restaurant, plan_code="starter")
    _stub_plain_reply(monkeypatch)
    _bulk_insert_conversations(db, second_restaurant, 300, channel="widget")

    response = client.post(f"/widget/{widget_key}/chat", json={"message": "hi"})
    assert response.status_code == 402
    assert response.json()["detail"]["error_code"] == "conversation_quota_exceeded"


def test_conversation_quota_ignores_conversations_from_a_previous_month(
    client, monkeypatch, admin_headers, second_restaurant, db
):
    now = datetime.utcnow()
    this_month_start = datetime(now.year, now.month, 1)
    last_month_moment = this_month_start - timedelta(days=1)

    _set_plan(client, admin_headers, second_restaurant, plan_code="starter")
    _stub_plain_reply(monkeypatch)
    # 300 conversations last month must not count toward this month's cap.
    _bulk_insert_conversations(db, second_restaurant, 300, when=last_month_moment)
    _bulk_insert_conversations(db, second_restaurant, 299, when=now)

    still_allowed = client.post("/chat", json={"message": "hi", "restaurant_id": second_restaurant})
    assert still_allowed.status_code == 200

    now_blocked = client.post("/chat", json={"message": "hi", "restaurant_id": second_restaurant})
    assert now_blocked.status_code == 402


def test_conversation_quota_block_writes_a_subscription_event(
    client, monkeypatch, admin_headers, second_restaurant, db
):
    _set_plan(client, admin_headers, second_restaurant, plan_code="starter")
    _stub_plain_reply(monkeypatch)
    _bulk_insert_conversations(db, second_restaurant, 300)

    response = client.post("/chat", json={"message": "hi", "restaurant_id": second_restaurant})
    assert response.status_code == 402

    events = _events_for(db, second_restaurant, "conversation_quota_exceeded")
    assert len(events) == 1
    assert '"plan_code": "starter"' in events[0].payload


# =========================================================
# Subscription status — active/trialing allowed, past_due/canceled blocked
# =========================================================

@pytest.mark.parametrize("allowed_status", ["active", "trialing"])
def test_chat_allowed_for_active_and_trialing(
    client, monkeypatch, admin_headers, second_restaurant, allowed_status
):
    _set_plan(client, admin_headers, second_restaurant, status=allowed_status)
    _stub_plain_reply(monkeypatch)
    response = client.post("/chat", json={"message": "hi", "restaurant_id": second_restaurant})
    assert response.status_code == 200


@pytest.mark.parametrize("blocked_status", ["past_due", "canceled"])
def test_chat_blocked_for_past_due_and_canceled(
    client, monkeypatch, admin_headers, second_restaurant, blocked_status
):
    _set_plan(client, admin_headers, second_restaurant, status=blocked_status)
    _stub_plain_reply(monkeypatch)
    response = client.post("/chat", json={"message": "hi", "restaurant_id": second_restaurant})
    assert response.status_code == 403
    assert response.json()["detail"]["error_code"] == "subscription_inactive"


def test_widget_chat_blocked_for_canceled_subscription(client, monkeypatch, admin_headers, second_restaurant):
    config_response = client.post(
        f"/admin/restaurant/{second_restaurant}/widget-config",
        json={"is_active": True},
        headers=admin_headers,
    )
    widget_key = config_response.json()["widget_key"]
    _set_plan(client, admin_headers, second_restaurant, status="canceled")
    _stub_plain_reply(monkeypatch)

    response = client.post(f"/widget/{widget_key}/chat", json={"message": "hi"})
    assert response.status_code == 403
    assert response.json()["detail"]["error_code"] == "subscription_inactive"


def test_canceled_subscription_does_not_lock_the_admin_dashboard(
    client, admin_headers, second_restaurant
):
    """Product Decision #3: a blocked subscription stops new customer AI
    service only — admin/booking-management endpoints stay reachable."""
    _set_plan(client, admin_headers, second_restaurant, status="canceled")
    response = client.get(f"/admin/restaurant/{second_restaurant}", headers=admin_headers)
    assert response.status_code == 200


def test_status_block_writes_no_subscription_event(
    client, monkeypatch, admin_headers, second_restaurant, db
):
    """Approved decision #6 lists exactly four event triggers; a
    subscription-status block itself is deliberately not one of them —
    the one event recorded here is from the PATCH's own plan/status-change
    trigger, not from the later blocked /chat attempt."""
    _set_plan(client, admin_headers, second_restaurant, status="canceled")
    _stub_plain_reply(monkeypatch)
    response = client.post("/chat", json={"message": "hi", "restaurant_id": second_restaurant})
    assert response.status_code == 403

    all_events = db.query(models.SubscriptionEvent).filter(
        models.SubscriptionEvent.restaurant_id == second_restaurant
    ).all()
    assert [e.event_type for e in all_events] == ["plan_or_status_changed"]


# =========================================================
# Admin-user quota
# =========================================================

@pytest.mark.parametrize("plan_code, limit", [("starter", 1), ("growth", 3), ("pro", 5)])
def test_admin_user_creation_allowed_up_to_plan_limit_then_blocked(
    client, admin_headers, second_restaurant, db, plan_code, limit
):
    _set_plan(client, admin_headers, second_restaurant, plan_code=plan_code)

    for i in range(limit):
        response = _create_admin_user(client, admin_headers, [second_restaurant], label=f"admin {i}")
        assert response.status_code == 201, f"admin #{i} unexpectedly blocked under {plan_code}'s cap of {limit}"

    over_limit = _create_admin_user(client, admin_headers, [second_restaurant], label="one too many")
    assert over_limit.status_code == 402
    body = over_limit.json()["detail"]
    assert body["error_code"] == "admin_user_quota_exceeded"
    assert str(limit) in body["message"]


def test_admin_quota_block_writes_a_subscription_event(client, admin_headers, second_restaurant, db):
    _set_plan(client, admin_headers, second_restaurant, plan_code="starter")
    _create_admin_user(client, admin_headers, [second_restaurant], label="first")
    over_limit = _create_admin_user(client, admin_headers, [second_restaurant], label="second")
    assert over_limit.status_code == 402

    events = _events_for(db, second_restaurant, "admin_user_quota_exceeded")
    assert len(events) == 1


def test_existing_admins_survive_a_downgrade_below_their_count(
    client, admin_headers, second_restaurant
):
    """Product Decision #4: never auto-deactivate existing admins after
    a downgrade — only NEW grants are blocked once already over cap."""
    _set_plan(client, admin_headers, second_restaurant, plan_code="growth")
    created_ids = []
    for i in range(3):
        response = _create_admin_user(client, admin_headers, [second_restaurant], label=f"survivor {i}")
        assert response.status_code == 201
        created_ids.append(response.json()["id"])

    _set_plan(client, admin_headers, second_restaurant, plan_code="starter")  # cap now 1, already 3 over

    for admin_user_id in created_ids:
        get_response = client.get(f"/admin/platform/admin-users/{admin_user_id}", headers=admin_headers)
        assert get_response.status_code == 200
        assert get_response.json()["is_active"] is True

    blocked = _create_admin_user(client, admin_headers, [second_restaurant], label="post-downgrade admin")
    assert blocked.status_code == 402


def test_restaurant_access_grant_respects_the_target_restaurants_own_quota(
    client, admin_headers, second_restaurant
):
    third = client.post(
        "/admin/platform/restaurants",
        json={
            "name": "The Anchor Grant Test", "address": "1 Quay St", "phone": "0117 000 0001",
            "email": "hello@anchor-granttest.co.uk", "seating_capacity": 20,
        },
        headers=admin_headers,
    ).json()["restaurant"]["id"]
    _set_plan(client, admin_headers, third, plan_code="starter")  # cap 1
    _create_admin_user(client, admin_headers, [third], label="fills up third restaurant")

    # An admin scoped elsewhere, trying to gain access to the already-full third restaurant.
    elsewhere = _create_admin_user(client, admin_headers, [second_restaurant], label="wants third too").json()
    grant = client.post(
        f"/admin/platform/admin-users/{elsewhere['id']}/restaurants/{third}", headers=admin_headers
    )
    assert grant.status_code == 402
    assert grant.json()["detail"]["error_code"] == "admin_user_quota_exceeded"


def test_regranting_existing_access_is_not_blocked_by_quota(client, admin_headers, second_restaurant):
    _set_plan(client, admin_headers, second_restaurant, plan_code="starter")
    admin_user = _create_admin_user(client, admin_headers, [second_restaurant], label="re-grant test").json()

    # Re-granting a restaurant this admin already has access to is a no-op, never quota-blocked.
    regrant = client.post(
        f"/admin/platform/admin-users/{admin_user['id']}/restaurants/{second_restaurant}",
        headers=admin_headers,
    )
    assert regrant.status_code == 200


# =========================================================
# WhatsApp plan gating
# =========================================================

def test_starter_plan_rejects_whatsapp_mapping(client, admin_headers, second_restaurant):
    _set_plan(client, admin_headers, second_restaurant, plan_code="starter")
    response = client.post(
        f"/admin/platform/restaurants/{second_restaurant}/whatsapp-number",
        json={"phone_number_id": "7000000000000001", "display_phone_number": "+15550009999"},
        headers=admin_headers,
    )
    assert response.status_code == 403
    assert response.json()["detail"]["error_code"] == "whatsapp_not_enabled_for_plan"


@pytest.mark.parametrize(
    "plan_code, phone_number_id",
    [("growth", "7000000000000101"), ("pro", "7000000000000102")],
)
def test_whatsapp_enabled_plans_allow_mapping(
    client, admin_headers, second_restaurant, plan_code, phone_number_id
):
    _set_plan(client, admin_headers, second_restaurant, plan_code=plan_code)
    response = client.post(
        f"/admin/platform/restaurants/{second_restaurant}/whatsapp-number",
        json={"phone_number_id": phone_number_id, "display_phone_number": "+15550009999"},
        headers=admin_headers,
    )
    assert response.status_code == 201


def test_whatsapp_mapping_block_writes_a_subscription_event(client, admin_headers, second_restaurant, db):
    _set_plan(client, admin_headers, second_restaurant, plan_code="starter")
    client.post(
        f"/admin/platform/restaurants/{second_restaurant}/whatsapp-number",
        json={"phone_number_id": "7000000000000002", "display_phone_number": "+15550009999"},
        headers=admin_headers,
    )
    events = _events_for(db, second_restaurant, "whatsapp_blocked_plan_not_enabled")
    assert len(events) == 1


def test_inbound_whatsapp_dropped_when_plan_disallows_it_despite_existing_mapping(
    client, monkeypatch, admin_headers, second_restaurant, db
):
    """Product Decision #5: a downgrade never deletes the mapping row,
    but inbound messages on it must stop being AI-processed."""
    _set_plan(client, admin_headers, second_restaurant, plan_code="starter")
    phone_number_id = "7000000000000003"
    db.add(models.WhatsAppNumber(
        restaurant_id=second_restaurant,
        phone_number_id=phone_number_id,
        display_phone_number="+15550009999",
    ))
    db.commit()

    call_count = {"n": 0}

    def fake_generate_content(model, contents, config):
        call_count["n"] += 1
        return _fake_response()

    monkeypatch.setattr(llm_module.client.models, "generate_content", fake_generate_content)

    process_incoming_message(
        phone_number_id,
        {"id": "wamid.enforce.1", "from": "447911100099", "type": "text", "text": {"body": "hi"}},
    )

    assert call_count["n"] == 0
    assert db.query(models.Message).filter(
        models.Message.external_message_id == "wamid.enforce.1"
    ).first() is None


def test_inbound_whatsapp_processed_normally_on_an_enabled_plan(
    client, monkeypatch, admin_headers, second_restaurant, db
):
    _set_plan(client, admin_headers, second_restaurant, plan_code="growth")
    phone_number_id = "7000000000000004"
    db.add(models.WhatsAppNumber(
        restaurant_id=second_restaurant,
        phone_number_id=phone_number_id,
        display_phone_number="+15550009999",
    ))
    db.commit()

    _stub_plain_reply(monkeypatch)
    monkeypatch.setattr(
        whatsapp_client, "send_text_message",
        lambda phone_number_id, to, text: None,
    )

    process_incoming_message(
        phone_number_id,
        {"id": "wamid.enforce.2", "from": "447911100098", "type": "text", "text": {"body": "hi"}},
    )

    db.expire_all()
    assert db.query(models.Message).filter(
        models.Message.external_message_id == "wamid.enforce.2"
    ).first() is not None


def test_inbound_whatsapp_respects_the_same_conversation_quota(
    client, monkeypatch, admin_headers, second_restaurant, db
):
    _set_plan(client, admin_headers, second_restaurant, plan_code="growth")  # WhatsApp-enabled, cap 1000
    phone_number_id = "7000000000000005"
    db.add(models.WhatsAppNumber(
        restaurant_id=second_restaurant,
        phone_number_id=phone_number_id,
        display_phone_number="+15550009999",
    ))
    db.commit()
    # Usage already at the cap (aggregated across every channel, including web/widget).
    _bulk_insert_conversations(db, second_restaurant, 1000)

    call_count = {"n": 0}

    def fake_generate_content(model, contents, config):
        call_count["n"] += 1
        return _fake_response()

    monkeypatch.setattr(llm_module.client.models, "generate_content", fake_generate_content)

    process_incoming_message(
        phone_number_id,
        {"id": "wamid.enforce.3", "from": "447911100097", "type": "text", "text": {"body": "hi"}},
    )

    assert call_count["n"] == 0
    assert db.query(models.Message).filter(
        models.Message.external_message_id == "wamid.enforce.3"
    ).first() is None


# =========================================================
# SubscriptionEvent on PATCH plan/status change
# =========================================================

def test_patch_plan_change_writes_a_subscription_event(client, admin_headers, second_restaurant, db):
    _set_plan(client, admin_headers, second_restaurant, plan_code="growth")
    events = _events_for(db, second_restaurant, "plan_or_status_changed")
    assert len(events) == 1
    assert '"old_plan_code": "starter"' in events[0].payload
    assert '"new_plan_code": "growth"' in events[0].payload


def test_patch_status_change_writes_a_subscription_event(client, admin_headers, second_restaurant, db):
    _set_plan(client, admin_headers, second_restaurant, status="past_due")
    events = _events_for(db, second_restaurant, "plan_or_status_changed")
    assert len(events) == 1
    assert '"old_status": "active"' in events[0].payload
    assert '"new_status": "past_due"' in events[0].payload


def test_patch_with_unchanged_values_writes_no_event(client, admin_headers, second_restaurant, db):
    _set_plan(client, admin_headers, second_restaurant, plan_code="starter", status="active")  # same as seeded default
    assert _events_for(db, second_restaurant, "plan_or_status_changed") == []


def test_subscription_events_are_append_only_across_multiple_changes(
    client, admin_headers, second_restaurant, db
):
    _set_plan(client, admin_headers, second_restaurant, plan_code="growth")
    _set_plan(client, admin_headers, second_restaurant, plan_code="pro")
    _set_plan(client, admin_headers, second_restaurant, status="canceled")

    events = _events_for(db, second_restaurant, "plan_or_status_changed")
    assert len(events) == 3


# =========================================================
# The Kings Arms (restaurant 1) — no regression
# =========================================================

def test_kings_arms_restaurant_1_is_not_broken_by_enforcement(client, monkeypatch, admin_headers):
    """Plan-agnostic on purpose: other test files in this shared session
    may have already changed restaurant 1's plan/status (e.g. the
    WhatsApp fixtures upgrading it to Growth — see
    tests/conftest.py:whatsapp_number). This only asserts the properties
    that must hold regardless: restaurant 1 still has exactly one
    subscription row, its usage is under whatever its current plan's
    limit is, and a normal chat request still succeeds end to end."""
    subscription_response = client.get("/admin/restaurant/1/subscription", headers=admin_headers)
    assert subscription_response.status_code == 200
    body = subscription_response.json()
    assert body["status"] in ("active", "trialing")
    assert body["usage"]["conversations_this_period"] < body["limits"]["max_conversations_per_month"]

    _stub_plain_reply(monkeypatch, "Welcome to The Kings Arms!")
    chat_response = client.post("/chat", json={"message": "Are you open today?", "restaurant_id": 1})
    assert chat_response.status_code == 200
