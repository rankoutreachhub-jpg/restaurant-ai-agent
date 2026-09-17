"""
POST /webhooks/paddle: Paddle-Signature verification (Jantar SaaS Phase
4 — subscription provisioning) and event processing, mirroring
tests/test_whatsapp_webhook_verification.py's structure/conventions for
the WhatsApp webhook.

Signature is computed over "{ts}:{raw_body}" — these tests build the
body as raw bytes and send it with content=..., never json=..., so what
gets signed and what gets sent are guaranteed byte-identical (the same
reason the WhatsApp webhook tests do this).

Restaurant association: see app/paddle_webhooks.py's module docstring.
These tests use `second_restaurant`'s id as data.custom_data.restaurant_id
to exercise the one deterministic linking path that exists today; tests
proving an UNASSOCIATED event is handled safely deliberately omit any
custom_data and use a subscription id no existing row is linked to.
"""

import hashlib
import hmac
import json
import time

from app import config, models

WEBHOOK_URL = "/webhooks/paddle"

GROWTH_PRICE_ID = "pri_01m2gw4307t3zt670b86mfzs5d"
PRO_PRICE_ID = "pri_01m2gw98h023yskcr1cfw3qhw6"
STARTER_PRICE_ID = "pri_01m2gvtqa1h6ecyq45nt61wkk8"
UNKNOWN_PRICE_ID = "pri_00000000000000000000000000"


def _sign(secret: str, ts: str, raw_body: bytes) -> str:
    signed_payload = ts.encode("utf-8") + b":" + raw_body
    digest = hmac.new(secret.encode("utf-8"), signed_payload, hashlib.sha256).hexdigest()
    return f"ts={ts};h1={digest}"


def _body(payload: dict) -> bytes:
    return json.dumps(payload).encode("utf-8")


def _post(client, payload: dict, *, secret=None, ts=None):
    secret = config.PADDLE_WEBHOOK_SECRET if secret is None else secret
    ts = ts or str(int(time.time()))
    raw_body = _body(payload)
    headers = {"Paddle-Signature": _sign(secret, ts, raw_body)}
    return client.post(WEBHOOK_URL, content=raw_body, headers=headers)


def _subscription_event(
    event_id, *, event_type="subscription.created", occurred_at="2026-09-17T12:00:00.000000Z",
    subscription_id="sub_test_000001", customer_id="ctm_test_000001", status="active",
    price_id=GROWTH_PRICE_ID, restaurant_id=None, current_period_end="2026-10-17T12:00:00.000000Z",
):
    data = {
        "id": subscription_id,
        "customer_id": customer_id,
        "status": status,
        "items": [{"price": {"id": price_id}, "quantity": 1}],
        "current_billing_period": {"ends_at": current_period_end} if current_period_end else {},
    }
    if restaurant_id is not None:
        data["custom_data"] = {"restaurant_id": restaurant_id}
    return {"event_id": event_id, "event_type": event_type, "occurred_at": occurred_at, "data": data}


def _transaction_completed_event(
    event_id, *, occurred_at="2026-09-17T12:05:00.000000Z", subscription_id="sub_test_000001",
    customer_id="ctm_test_000001", price_id=GROWTH_PRICE_ID, restaurant_id=None,
):
    data = {
        "id": "txn_test_000001",
        "subscription_id": subscription_id,
        "customer_id": customer_id,
        "items": [{"price": {"id": price_id}}],
    }
    if restaurant_id is not None:
        data["custom_data"] = {"restaurant_id": restaurant_id}
    return {"event_id": event_id, "event_type": "transaction.completed", "occurred_at": occurred_at, "data": data}


def _get_subscription(db, restaurant_id):
    return db.query(models.Subscription).filter(models.Subscription.restaurant_id == restaurant_id).one()


def _get_webhook_event(db, event_id):
    return db.query(models.PaddleWebhookEvent).filter(models.PaddleWebhookEvent.event_id == event_id).one()


# --- Signature verification ---

def test_post_with_valid_signature_is_accepted(client):
    payload = _subscription_event("evt_sig_valid_1")
    response = _post(client, payload)
    assert response.status_code == 200


def test_post_with_invalid_signature_is_rejected(client):
    payload = _subscription_event("evt_sig_invalid_1")
    raw_body = _body(payload)
    response = client.post(
        WEBHOOK_URL, content=raw_body,
        headers={"Paddle-Signature": "ts=1700000000;h1=" + "0" * 64},
    )
    assert response.status_code == 401


def test_post_with_missing_signature_is_rejected(client):
    payload = _subscription_event("evt_sig_missing_1")
    response = client.post(WEBHOOK_URL, content=_body(payload))
    assert response.status_code == 401


def test_post_with_malformed_signature_header_is_rejected(client):
    payload = _subscription_event("evt_sig_malformed_1")
    response = client.post(
        WEBHOOK_URL, content=_body(payload), headers={"Paddle-Signature": "not-a-valid-header"}
    )
    assert response.status_code == 401


def test_post_is_rejected_when_secret_not_configured(client, monkeypatch):
    monkeypatch.setattr(config, "PADDLE_WEBHOOK_SECRET", "")
    payload = _subscription_event("evt_sig_no_secret_1")
    response = _post(client, payload, secret="whatever-the-test-tries-to-sign-with")
    assert response.status_code == 401


def test_post_signature_for_a_different_body_is_rejected(client):
    """Proves the signature is checked against the RAW body actually
    sent, not some other representation of it."""
    real_payload = _subscription_event("evt_sig_body_mismatch_1")
    real_body = _body(real_payload)
    ts = str(int(time.time()))
    signature_for_different_body = _sign(
        config.PADDLE_WEBHOOK_SECRET, ts, _body(_subscription_event("evt_totally_different"))
    )
    response = client.post(
        WEBHOOK_URL, content=real_body, headers={"Paddle-Signature": signature_for_different_body}
    )
    assert response.status_code == 401


def test_post_with_invalid_json_after_valid_signature_is_rejected(client):
    raw_body = b"this is not json"
    ts = str(int(time.time()))
    response = client.post(
        WEBHOOK_URL, content=raw_body,
        headers={"Paddle-Signature": _sign(config.PADDLE_WEBHOOK_SECRET, ts, raw_body)},
    )
    assert response.status_code == 400


def test_post_rejects_before_touching_the_database(client, monkeypatch):
    """Signature verification must happen before any event processing —
    proven by asserting process_paddle_webhook_event is never invoked
    for an invalid-signature request."""
    from app.routers import paddle as paddle_router

    calls = []
    monkeypatch.setattr(paddle_router, "process_paddle_webhook_event", lambda *a, **kw: calls.append((a, kw)))

    payload = _subscription_event("evt_sig_no_db_touch_1")
    response = client.post(
        WEBHOOK_URL, content=_body(payload),
        headers={"Paddle-Signature": "ts=1700000000;h1=" + "0" * 64},
    )
    assert response.status_code == 401
    assert calls == []


# --- Event processing: association, mapping, and state changes ---

def test_subscription_created_applies_plan_status_and_provider_fields(client, second_restaurant, db):
    payload = _subscription_event(
        "evt_created_1", subscription_id="sub_created_1", customer_id="ctm_created_1",
        status="active", price_id=GROWTH_PRICE_ID, restaurant_id=second_restaurant,
    )
    response = _post(client, payload)
    assert response.status_code == 200

    subscription = _get_subscription(db, second_restaurant)
    assert subscription.plan_code == "growth"
    assert subscription.status == "active"
    assert subscription.billing_provider == "paddle"
    assert subscription.provider_customer_id == "ctm_created_1"
    assert subscription.provider_subscription_id == "sub_created_1"
    assert subscription.provider_price_id == GROWTH_PRICE_ID
    assert subscription.current_period_end is not None

    event = (
        db.query(models.SubscriptionEvent)
        .filter(models.SubscriptionEvent.restaurant_id == second_restaurant)
        .filter(models.SubscriptionEvent.event_type == "paddle_subscription.created")
        .one()
    )
    assert json.loads(event.payload)["new_plan_code"] == "growth"


def test_subscription_updated_changes_plan_and_status(client, second_restaurant, db):
    _post(client, _subscription_event(
        "evt_update_base_1", subscription_id="sub_update_1", restaurant_id=second_restaurant,
        price_id=GROWTH_PRICE_ID, status="active", occurred_at="2026-09-17T12:00:00.000000Z",
    ))
    response = _post(client, _subscription_event(
        "evt_update_2", event_type="subscription.updated", subscription_id="sub_update_1",
        price_id=PRO_PRICE_ID, status="active", occurred_at="2026-09-17T13:00:00.000000Z",
    ))
    assert response.status_code == 200

    subscription = _get_subscription(db, second_restaurant)
    assert subscription.plan_code == "pro"
    assert subscription.provider_price_id == PRO_PRICE_ID


def test_subscription_canceled_sets_status_canceled(client, second_restaurant, db):
    _post(client, _subscription_event(
        "evt_cancel_base_1", subscription_id="sub_cancel_1", restaurant_id=second_restaurant,
        status="active", occurred_at="2026-09-17T12:00:00.000000Z",
    ))
    response = _post(client, _subscription_event(
        "evt_cancel_2", event_type="subscription.canceled", subscription_id="sub_cancel_1",
        status="canceled", occurred_at="2026-09-17T13:00:00.000000Z",
    ))
    assert response.status_code == 200
    assert _get_subscription(db, second_restaurant).status == "canceled"


def test_subscription_past_due_sets_status_past_due(client, second_restaurant, db):
    _post(client, _subscription_event(
        "evt_pastdue_base_1", subscription_id="sub_pastdue_1", restaurant_id=second_restaurant,
        status="active", occurred_at="2026-09-17T12:00:00.000000Z",
    ))
    response = _post(client, _subscription_event(
        "evt_pastdue_2", event_type="subscription.past_due", subscription_id="sub_pastdue_1",
        status="past_due", occurred_at="2026-09-17T13:00:00.000000Z",
    ))
    assert response.status_code == 200
    assert _get_subscription(db, second_restaurant).status == "past_due"


def test_transaction_completed_activates_and_maps_price(client, second_restaurant, db):
    _post(client, _subscription_event(
        "evt_txn_base_1", subscription_id="sub_txn_1", restaurant_id=second_restaurant,
        status="past_due", price_id=STARTER_PRICE_ID, occurred_at="2026-09-17T12:00:00.000000Z",
    ))
    response = _post(client, _transaction_completed_event(
        "evt_txn_2", subscription_id="sub_txn_1", price_id=GROWTH_PRICE_ID,
        occurred_at="2026-09-17T13:00:00.000000Z",
    ))
    assert response.status_code == 200

    subscription = _get_subscription(db, second_restaurant)
    assert subscription.status == "active"
    assert subscription.plan_code == "growth"
    assert subscription.provider_price_id == GROWTH_PRICE_ID


# --- Idempotency ---

def test_duplicate_event_id_does_not_apply_twice(client, second_restaurant, db):
    payload = _subscription_event(
        "evt_dup_1", subscription_id="sub_dup_1", restaurant_id=second_restaurant,
        price_id=GROWTH_PRICE_ID, status="active",
    )
    first = _post(client, payload)
    second = _post(client, payload)
    assert first.status_code == 200
    assert second.status_code == 200

    events = (
        db.query(models.SubscriptionEvent)
        .filter(models.SubscriptionEvent.restaurant_id == second_restaurant)
        .filter(models.SubscriptionEvent.event_type == "paddle_subscription.created")
        .all()
    )
    assert len(events) == 1, "redelivery of the same event_id must not write a second SubscriptionEvent"

    webhook_rows = db.query(models.PaddleWebhookEvent).filter(models.PaddleWebhookEvent.event_id == "evt_dup_1").all()
    assert len(webhook_rows) == 1, "redelivery of the same event_id must not write a second PaddleWebhookEvent row"


# --- Unknown price id / unmappable restaurant ---

def test_unknown_paddle_price_id_leaves_plan_code_unchanged(client, second_restaurant, db):
    baseline = _get_subscription(db, second_restaurant)
    assert baseline.plan_code == "starter"

    response = _post(client, _subscription_event(
        "evt_unknown_price_1", subscription_id="sub_unknown_price_1", restaurant_id=second_restaurant,
        price_id=UNKNOWN_PRICE_ID, status="active",
    ))
    assert response.status_code == 200

    subscription = _get_subscription(db, second_restaurant)
    assert subscription.plan_code == "starter", "an unmapped price id must never change plan_code"
    assert subscription.status == "active", "other, understood fields must still be applied"

    flagged = (
        db.query(models.SubscriptionEvent)
        .filter(models.SubscriptionEvent.restaurant_id == second_restaurant)
        .filter(models.SubscriptionEvent.event_type == "paddle_unknown_price_id")
        .one()
    )
    assert json.loads(flagged.payload)["price_id"] == UNKNOWN_PRICE_ID


def test_unassociated_event_is_logged_but_touches_no_subscription(client, db):
    """No custom_data and no pre-existing link for this Paddle
    subscription id -- there is no deterministic way to know which
    restaurant (if any) this belongs to, so nothing is guessed and no
    Subscription row anywhere is touched."""
    before_count = db.query(models.Subscription).filter(models.Subscription.provider_subscription_id == "sub_orphan_1").count()
    assert before_count == 0

    response = _post(client, _subscription_event(
        "evt_orphan_1", subscription_id="sub_orphan_1", status="active",
    ))
    assert response.status_code == 200

    after_count = db.query(models.Subscription).filter(models.Subscription.provider_subscription_id == "sub_orphan_1").count()
    assert after_count == 0, "an unassociated event must never get linked to any restaurant's subscription"

    webhook_row = _get_webhook_event(db, "evt_orphan_1")
    assert webhook_row.restaurant_id is None
    assert webhook_row.applied is False
    assert webhook_row.provider_subscription_id == "sub_orphan_1"


# --- Out-of-order events ---

def test_out_of_order_event_does_not_regress_applied_state(client, second_restaurant, db):
    _post(client, _subscription_event(
        "evt_order_newer_1", subscription_id="sub_order_1", restaurant_id=second_restaurant,
        status="active", price_id=PRO_PRICE_ID, occurred_at="2026-09-17T15:00:00.000000Z",
    ))
    stale_response = _post(client, _subscription_event(
        "evt_order_older_2", event_type="subscription.updated", subscription_id="sub_order_1",
        status="canceled", price_id=STARTER_PRICE_ID, occurred_at="2026-09-17T10:00:00.000000Z",
    ))
    assert stale_response.status_code == 200

    subscription = _get_subscription(db, second_restaurant)
    assert subscription.status == "active", "an older, out-of-order event must not overwrite newer applied state"
    assert subscription.plan_code == "pro"

    stale_row = _get_webhook_event(db, "evt_order_older_2")
    assert stale_row.applied is False


def test_events_applied_in_true_chronological_order_both_take_effect(client, second_restaurant, db):
    _post(client, _subscription_event(
        "evt_order_first_1", subscription_id="sub_order_2", restaurant_id=second_restaurant,
        status="active", price_id=STARTER_PRICE_ID, occurred_at="2026-09-17T10:00:00.000000Z",
    ))
    _post(client, _subscription_event(
        "evt_order_second_2", event_type="subscription.updated", subscription_id="sub_order_2",
        status="active", price_id=PRO_PRICE_ID, occurred_at="2026-09-17T15:00:00.000000Z",
    ))
    subscription = _get_subscription(db, second_restaurant)
    assert subscription.plan_code == "pro"


# --- Event types outside the required 5 ---

def test_unhandled_event_type_is_logged_and_ignored(client, second_restaurant, db):
    payload = _subscription_event(
        "evt_unhandled_1", event_type="subscription.paused", subscription_id="sub_unhandled_1",
        restaurant_id=second_restaurant,
    )
    response = _post(client, payload)
    assert response.status_code == 200

    row = _get_webhook_event(db, "evt_unhandled_1")
    assert row.applied is False
    subscription = _get_subscription(db, second_restaurant)
    assert subscription.provider_subscription_id != "sub_unhandled_1"
