"""
Application logging: console output plus a rotating log file, so errors
and important events (admin auth failures, rate limiting, seeding,
unhandled /chat errors) survive a restart instead of only living in
whatever terminal happened to be open.

What gets logged, deliberately:
  - Timestamps and levels on every line (see _LOG_FORMAT below).
  - Application errors and warnings — see app/routers/chat.py,
    app/auth.py, app/rate_limit.py.

What never gets logged, by construction of the call sites above:
  - API keys or admin keys (GEMINI_API_KEY, ADMIN_API_KEY /
    ADMIN_API_KEY_PREVIOUS) — auth failures log only the client IP and
    request path, never the submitted header value.
  - Authentication headers or full request/response bodies.
  - Customer chat message content — unhandled /chat errors log a fixed
    description plus the exception/traceback, not the request payload.

Rotation: once app.log reaches LOG_MAX_BYTES, it's renamed app.log.1
(app.log.1 -> app.log.2, etc.) and a fresh app.log is started, keeping
at most LOG_BACKUP_COUNT old files — bounded disk use without needing
an external log-management service, which is all this MVP needs.
"""

import logging
import logging.handlers

from . import config

LOG_FILE = config.LOG_DIR / "app.log"

LOG_MAX_BYTES = 1_000_000  # rotate once a log file reaches ~1 MB
LOG_BACKUP_COUNT = 5  # keep app.log plus up to 5 rotated backups

_LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def configure_logging() -> None:
    """
    Attaches a console handler and a rotating file handler to the root
    logger, so every module's logging.getLogger(__name__) call reaches
    both. Safe to call more than once (e.g. across test runs) — clears
    any handlers it previously added first, rather than stacking them.
    """
    config.LOG_DIR.mkdir(parents=True, exist_ok=True)

    formatter = logging.Formatter(_LOG_FORMAT)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)

    file_handler = logging.handlers.RotatingFileHandler(
        LOG_FILE,
        maxBytes=LOG_MAX_BYTES,
        backupCount=LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    root_logger.handlers.clear()
    root_logger.addHandler(console_handler)
    root_logger.addHandler(file_handler)
