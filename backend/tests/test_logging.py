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
