"""
Centralised configuration loading.

WHY THIS FILE EXISTS:
Previously, environment variables were read with plain os.getenv() calls
scattered across files, and nothing ever loaded the .env file itself —
so GEMINI_API_KEY only worked if you manually exported it in your
terminal every session. This file fixes that by:

  1. Loading backend/.env automatically using python-dotenv, using an
     explicit path so it works no matter which folder you run the
     server from (this matters especially on Windows).
  2. Giving every other file ONE place to import settings from.
  3. Providing validate_config(), which is called before the server
     starts, so a missing API key produces one clear error message
     instead of a confusing failure on the first chat request.
"""

import os
import sys
from pathlib import Path
from dotenv import load_dotenv

# backend/app/config.py -> parent is backend/app, parent.parent is backend/
# This gives us the absolute path to backend/.env regardless of the
# current working directory the server was launched from.
BACKEND_DIR = Path(__file__).resolve().parent.parent
ENV_PATH = BACKEND_DIR / ".env"

# Load the .env file if it exists. If it doesn't exist, this simply does
# nothing (no error) — validate_config() below is what catches a missing key.
load_dotenv(dotenv_path=ENV_PATH)

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()

# Shared secret required in the "X-Admin-API-Key" header on every /admin/*
# request (see app/auth.py). Simple pre-shared-key auth, not user accounts —
# appropriate for a single-operator MVP admin surface.
ADMIN_API_KEY = os.getenv("ADMIN_API_KEY", "").strip()

# Optional previous admin key, still accepted alongside ADMIN_API_KEY
# during a rotation so existing clients aren't locked out mid-rotation.
# Unset (the default) means no previous key is honoured. See README.md
# ("Rotating the admin key") for the full procedure.
ADMIN_API_KEY_PREVIOUS = os.getenv("ADMIN_API_KEY_PREVIOUS", "").strip()

# Default DB path is absolute (based on this file's location) rather than
# relative, so the database always lands in backend/restaurant.db no matter
# which folder you happen to run the server from. This matters especially
# on Windows, where users often run commands from unexpected directories.
_default_db_path = BACKEND_DIR / "restaurant.db"
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{_default_db_path}").strip()

# Browser origins allowed to call this API (CORS), comma-separated.
# Defaults cover common local dev setups out of the box:
#   - localhost/127.0.0.1 on the ports a simple static-file server or
#     frontend dev server would typically use
#   - "null", the literal Origin value browsers send for a page opened
#     directly as a local file (e.g. double-clicking frontend/index.html,
#     as this project's README currently instructs)
# Before deploying, set ALLOWED_ORIGINS to your real frontend domain(s)
# and drop the localhost/"null" defaults.
_DEFAULT_ALLOWED_ORIGINS = (
    "http://localhost:3000,http://127.0.0.1:3000,"
    "http://localhost:5500,http://127.0.0.1:5500,"
    "http://localhost:8000,http://127.0.0.1:8000,"
    "null"
)
ALLOWED_ORIGINS = [
    origin.strip()
    for origin in os.getenv("ALLOWED_ORIGINS", _DEFAULT_ALLOWED_ORIGINS).split(",")
    if origin.strip()
]

# Directory for the rotating application log file (see
# app/logging_config.py). Defaults to backend/logs/, absolute for the
# same reason DATABASE_URL is: it should land in the same place no
# matter which folder the server was launched from.
LOG_DIR = Path(os.getenv("LOG_DIR", str(BACKEND_DIR / "logs"))).resolve()


def validate_config():
    """
    Checks that all required configuration is present. Called once at
    server startup (see main.py). If something required is missing, we
    print a clear, actionable error message and exit immediately —
    rather than starting a server that will fail confusingly on the
    first real chat request.
    """
    problems = []

    if not GEMINI_API_KEY:
        problems.append(
            "GEMINI_API_KEY is missing.\n"
            f"  Expected it in: {ENV_PATH}\n"
            "  Fix: copy .env.example to .env and paste in your real Gemini API key.\n"
            "  Get one at: https://aistudio.google.com/apikey\n"
            "  Example .env content:\n"
            "    GEMINI_API_KEY=AIzaSyxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
        )
    elif not GEMINI_API_KEY.startswith("AIza"):
        # Not a hard failure (key formats can change), but a useful sanity warning.
        print(
            "WARNING: GEMINI_API_KEY does not look like a typical Google AI key "
            "(expected it to start with 'AIza'). If chat requests fail with an "
            "authentication error, double-check the key in your .env file."
        )

    if not ADMIN_API_KEY:
        problems.append(
            "ADMIN_API_KEY is missing.\n"
            f"  Expected it in: {ENV_PATH}\n"
            "  Fix: add a long random secret to your .env file. Generate one with:\n"
            "    python -c \"import secrets; print(secrets.token_urlsafe(32))\"\n"
            "  Example .env content:\n"
            "    ADMIN_API_KEY=your-generated-secret-here"
        )
    elif len(ADMIN_API_KEY) < 16:
        # Not a hard failure, but a short/guessable secret defeats the point.
        print(
            "WARNING: ADMIN_API_KEY is shorter than 16 characters. Use a longer, "
            "randomly generated secret to protect the admin endpoints."
        )

    if ADMIN_API_KEY_PREVIOUS:
        if ADMIN_API_KEY_PREVIOUS == ADMIN_API_KEY:
            print(
                "WARNING: ADMIN_API_KEY_PREVIOUS is identical to ADMIN_API_KEY, "
                "so it has no effect. Remove it once a rotation is complete."
            )
        elif len(ADMIN_API_KEY_PREVIOUS) < 16:
            print(
                "WARNING: ADMIN_API_KEY_PREVIOUS is shorter than 16 characters. "
                "Use a long, randomly generated secret."
            )

    if problems:
        print("\n" + "=" * 70)
        print("STARTUP FAILED — configuration problem(s) found:")
        print("=" * 70)
        for i, problem in enumerate(problems, start=1):
            print(f"\n{i}. {problem}")
        print("\n" + "=" * 70 + "\n")
        sys.exit(1)
