"""
app/paddle_checkout_tokens.py: pure unit tests for issue_checkout_token/
verify_checkout_token (Jantar SaaS Phase 4.1 — authenticated Paddle
checkout association). No DB, no HTTP — see that module's own docstring
for why it's deliberately pure and what security property each check
below defends.
"""

import time

from app import config, paddle_checkout_tokens as tokens


def test_valid_token_round_trips_to_the_same_restaurant_id():
    token = tokens.issue_checkout_token(42)
    assert tokens.verify_checkout_token(token) == 42


def test_two_tokens_for_the_same_restaurant_are_not_identical():
    """Each token carries its own random nonce -- issuing twice for the
    same restaurant must not produce byte-identical tokens."""
    first = tokens.issue_checkout_token(7)
    second = tokens.issue_checkout_token(7)
    assert first != second
    assert tokens.verify_checkout_token(first) == 7
    assert tokens.verify_checkout_token(second) == 7


def test_payload_tampering_is_rejected():
    """Swapping the payload segment for a DIFFERENT restaurant's
    legitimately-issued payload (signature now mismatches) must not
    verify -- this is the exact cross-tenant forgery the whole feature
    exists to prevent."""
    token_for_restaurant_a = tokens.issue_checkout_token(1)
    token_for_restaurant_b = tokens.issue_checkout_token(2)
    a_payload, _, _ = token_for_restaurant_a.partition(".")
    _, _, b_signature = token_for_restaurant_b.partition(".")

    forged = f"{a_payload}.{b_signature}"
    assert tokens.verify_checkout_token(forged) is None


def test_signature_tampering_is_rejected():
    token = tokens.issue_checkout_token(42)
    payload_b64, _, signature = token.partition(".")
    tampered_signature = ("0" if signature[0] != "0" else "1") + signature[1:]
    assert tokens.verify_checkout_token(f"{payload_b64}.{tampered_signature}") is None


def test_truncated_signature_is_rejected():
    token = tokens.issue_checkout_token(42)
    payload_b64, _, signature = token.partition(".")
    assert tokens.verify_checkout_token(f"{payload_b64}.{signature[:-4]}") is None


def test_expired_token_is_rejected(monkeypatch):
    monkeypatch.setattr(tokens, "TOKEN_LIFETIME_SECONDS", -1)
    expired = tokens.issue_checkout_token(42)
    assert tokens.verify_checkout_token(expired) is None


def test_token_signed_with_a_different_secret_is_rejected(monkeypatch):
    token = tokens.issue_checkout_token(42)
    monkeypatch.setattr(config, "PADDLE_CHECKOUT_TOKEN_SECRET", "a-completely-different-secret")
    assert tokens.verify_checkout_token(token) is None


def test_verification_fails_closed_when_secret_not_configured(monkeypatch):
    token = tokens.issue_checkout_token(42)
    monkeypatch.setattr(config, "PADDLE_CHECKOUT_TOKEN_SECRET", "")
    assert tokens.verify_checkout_token(token) is None


def test_malformed_token_shapes_are_all_rejected():
    for malformed in ("", "not-a-token-at-all", ".", "onlyonesegment", "..", "a.b.c"):
        assert tokens.verify_checkout_token(malformed) is None


def test_empty_or_missing_segments_are_rejected():
    token = tokens.issue_checkout_token(42)
    payload_b64, _, signature = token.partition(".")
    assert tokens.verify_checkout_token(f".{signature}") is None
    assert tokens.verify_checkout_token(f"{payload_b64}.") is None


def test_garbage_payload_segment_is_rejected():
    """A payload segment that doesn't even base64-decode to valid JSON
    must fail closed, not raise."""
    assert tokens.verify_checkout_token("not-valid-base64-!!!.deadbeef") is None


def test_token_lifetime_constant_is_fifteen_minutes():
    assert tokens.TOKEN_LIFETIME_SECONDS == 15 * 60


def test_issued_token_expiry_is_approximately_fifteen_minutes_out():
    before = int(time.time())
    token = tokens.issue_checkout_token(42)
    payload_b64, _, _ = token.partition(".")
    import base64
    import json
    padding = "=" * (-len(payload_b64) % 4)
    payload = json.loads(base64.urlsafe_b64decode(payload_b64 + padding))
    after = int(time.time())
    assert before + tokens.TOKEN_LIFETIME_SECONDS <= payload["exp"] <= after + tokens.TOKEN_LIFETIME_SECONDS
