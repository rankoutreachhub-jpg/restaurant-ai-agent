"""
Application logging: a rotating log file must exist alongside console
output, carry timestamps/levels, capture admin-auth failures and rate
limit events, and never contain the actual admin key or a rejected
request's submitted key value.
"""

import logging
import logging.handlers

from app import config
from app.logging_config import LOG_FILE
from app.rate_limit import admin_rate_limiter


def _read_log() -> str:
    return LOG_FILE.read_text(encoding="utf-8")


def test_log_file_exists_after_app_startup():
    assert config.LOG_DIR.exists()
    assert LOG_FILE.exists()


def test_configure_logging_sets_up_console_and_rotating_file_handlers():
    # pytest's own log-capture plugin adds handlers of its own to the
    # root logger, so we can't just inspect it as the app left it —
    # call configure_logging() ourselves and inspect the handlers it
    # installs in isolation, restoring whatever was there afterwards so
    # this doesn't disturb other tests' use of caplog.
    from app.logging_config import configure_logging

    root_logger = logging.getLogger()
    original_handlers = root_logger.handlers[:]
    try:
        configure_logging()
        handlers = root_logger.handlers

        rotating_handlers = [
            h for h in handlers if isinstance(h, logging.handlers.RotatingFileHandler)
        ]
        assert len(rotating_handlers) == 1
        assert rotating_handlers[0].baseFilename == str(LOG_FILE)

        # RotatingFileHandler is itself a StreamHandler subclass, so an
        # exact-type check is what isolates the plain console handler.
        console_handlers = [h for h in handlers if type(h) is logging.StreamHandler]
        assert len(console_handlers) == 1
    finally:
        root_logger.handlers = original_handlers


def test_log_entries_include_timestamp_and_level():
    logger = logging.getLogger("test.format_check")
    marker = "format-check-marker-4f8a1c"
    logger.info(marker)

    content = _read_log()
    assert marker in content
    line = next(line for line in content.splitlines() if marker in line)
    assert "INFO" in line
    # crude but effective: a formatted asctime starts with a 4-digit year
    assert line[:4].isdigit()


def test_admin_auth_failure_is_logged_without_leaking_any_key(client):
    guessed_key = "an-attackers-guessed-admin-key-value"
    response = client.get("/admin/restaurant/1", headers={"X-Admin-API-Key": guessed_key})
    assert response.status_code == 401

    content = _read_log()
    assert "Admin authentication failed" in content
    assert guessed_key not in content
    assert config.ADMIN_API_KEY not in content


def test_rate_limit_exceeded_is_logged(client, admin_headers):
    for _ in range(admin_rate_limiter.max_requests):
        client.get("/admin/restaurant/1", headers=admin_headers)
    response = client.get("/admin/restaurant/1", headers=admin_headers)
    assert response.status_code == 429

    content = _read_log()
    assert "Rate limit exceeded" in content
    assert "limiter=admin" in content
    # The valid admin key used to make these requests must never appear.
    assert config.ADMIN_API_KEY not in content


def test_admin_user_key_creation_and_rotation_never_logs_plaintext_key(
    client, second_restaurant, admin_headers
):
    created = client.post(
        "/admin/platform/admin-users",
        json={"label": "log check", "restaurant_ids": [second_restaurant]},
        headers=admin_headers,
    ).json()
    plaintext_key = created["api_key"]
    admin_user_id = created["id"]

    rotated = client.post(
        f"/admin/platform/admin-users/{admin_user_id}/rotate-key", headers=admin_headers
    ).json()
    rotated_key = rotated["api_key"]

    content = _read_log()
    assert plaintext_key not in content
    assert rotated_key not in content
    # The secret half specifically must never appear, even split across
    # a differently-formatted log line.
    assert plaintext_key.split(".")[1] not in content
    assert rotated_key.split(".")[1] not in content


def test_chat_message_content_and_conversation_token_are_never_logged(client, monkeypatch):
    """
    Stage 3 Step 5: persisted message content can genuinely contain
    customer PII (same trust boundary as the existing Booking table),
    and the conversation_token is the resumption handle for that
    content — neither may ever reach app.log, extending this project's
    existing no-PII/no-secrets logging policy to conversation
    persistence.
    """
    from app import llm

    def fake_generate_content(model, contents, config):
        from types import SimpleNamespace
        return SimpleNamespace(
            text="Reply text that must not be logged either",
            function_calls=[],
            candidates=[SimpleNamespace(content=SimpleNamespace(role="model", parts=[]))],
        )

    monkeypatch.setattr(llm.client.models, "generate_content", fake_generate_content)

    secret_marker = "my-phone-is-07000-logging-marker-99881"
    response = client.post(
        "/chat", json={"message": secret_marker, "history": [], "restaurant_id": 1}
    )
    assert response.status_code == 200
    token = response.headers["X-Conversation-Token"]

    content = _read_log()
    assert secret_marker not in content
    assert "Reply text that must not be logged either" not in content
    assert token not in content


def test_unhandled_chat_error_is_logged_to_file(client, monkeypatch):
    from app import llm

    def _boom(**kwargs):
        raise RuntimeError("simulated upstream failure")

    monkeypatch.setattr(llm, "generate_reply", _boom)

    response = client.post(
        "/chat",
        json={"message": "hi", "history": [], "restaurant_id": 1},
    )
    assert response.status_code == 500

    content = _read_log()
    assert "Unhandled error while generating a chat reply" in content
