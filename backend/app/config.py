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

# Default DB path is absolute (based on this file's location) rather than
# relative, so the database always lands in backend/restaurant.db no matter
# which folder you happen to run the server from. This matters especially
# on Windows, where users often run commands from unexpected directories.
_default_db_path = BACKEND_DIR / "restaurant.db"
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{_default_db_path}").strip()


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

    if problems:
        print("\n" + "=" * 70)
        print("STARTUP FAILED — configuration problem(s) found:")
        print("=" * 70)
        for i, problem in enumerate(problems, start=1):
            print(f"\n{i}. {problem}")
        print("\n" + "=" * 70 + "\n")
        sys.exit(1)
