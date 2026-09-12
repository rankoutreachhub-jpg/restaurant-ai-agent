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

# Which Gemini model app/llm.py calls. Kept configurable (rather than a
# hardcoded literal) so a future Google model retirement — exactly what
# took gemini-2.5-flash down in production — is a one-line environment
# variable change, not a code deploy.
GEMINI_MODEL_NAME = os.getenv("GEMINI_MODEL_NAME", "gemini-3.6-flash").strip()

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

# --- Trusted proxy / real client IP (Security & Production Hardening
# Audit finding C1) ---
# request.client.host (read by app/rate_limit.py and app/auth.py — see
# both modules' own docstrings) is populated by uvicorn's
# ProxyHeadersMiddleware, not by this app's code: uvicorn's
# --proxy-headers is already on by default (see the Dockerfile CMD,
# which doesn't pass --no-proxy-headers) and rewrites it from the
# X-Forwarded-For header, but ONLY when the immediate connecting peer's
# address is in --forwarded-allow-ips / the FORWARDED_ALLOW_IPS
# environment variable (uvicorn's own default: "127.0.0.1", i.e.
# trust nobody except a same-host reverse proxy). Behind Railway's edge,
# the connecting peer is Railway's own proxy, not 127.0.0.1, so with
# nothing configured, request.client.host is Railway's proxy address on
# every request, not the real visitor's — silently degrading every
# per-IP rate limiter (app/rate_limit.py) into one shared global bucket,
# and making the failed-admin-auth log (app/auth.py) show the proxy's
# address instead of an attacker's.
#
# This value is read here ONLY so validate_config() below can warn about
# an unsafe setting — app/config.py does not use it for anything at
# runtime, and setting it in backend/.env has no effect on uvicorn's own
# decision (uvicorn resolves it from a real process environment variable
# before this module is ever imported, so it must be exported in the
# actual shell/process environment locally, or set as a real Railway
# environment variable in production — both of which are already true
# for GEMINI_API_KEY/ADMIN_API_KEY today).
#
# Left UNSET by default (preserving today's behavior exactly): setting
# this to your confirmed trusted proxy IP(s) — never "*", which trusts
# whatever the ORIGINAL, unauthenticated client itself claims as the
# first hop in X-Forwarded-For — is a deployment-topology decision for
# whoever configures the real Railway service, not something this
# codebase can safely guess or default to.
FORWARDED_ALLOW_IPS = os.getenv("FORWARDED_ALLOW_IPS", "").strip()

# Directory for the rotating application log file (see
# app/logging_config.py). Defaults to backend/logs/, absolute for the
# same reason DATABASE_URL is: it should land in the same place no
# matter which folder the server was launched from.
LOG_DIR = Path(os.getenv("LOG_DIR", str(BACKEND_DIR / "logs"))).resolve()

# --- WhatsApp integration (Stage 3 Step 6B) ---
# All four are optional: WhatsApp is an opt-in feature (a restaurant
# only gets a phone_number_id mapping — see app/models.py:WhatsAppNumber
# — once an operator sets one up), so unlike GEMINI_API_KEY/
# ADMIN_API_KEY above, none of these are required at startup, and CI
# never needs real Meta credentials to run. Left unset, the webhook
# endpoints simply reject every request (fail closed — see
# app/routers/whatsapp.py) rather than the server refusing to start.

# The value Meta's webhook GET-verification request must present as
# hub.verify_token (see app/routers/whatsapp.py) — set this to whatever
# you enter as the "Verify token" when configuring the webhook in the
# Meta App Dashboard.
WHATSAPP_VERIFY_TOKEN = os.getenv("WHATSAPP_VERIFY_TOKEN", "").strip()

# Your Meta app's App Secret, used to verify the X-Hub-Signature-256
# HMAC-SHA256 header on every webhook POST (see app/routers/whatsapp.py)
# — proves a webhook request genuinely came from Meta before anything
# in it is trusted or touches the database.
WHATSAPP_APP_SECRET = os.getenv("WHATSAPP_APP_SECRET", "").strip()

# Access token used to call the Meta Graph "send message" API (see
# app/whatsapp_client.py). v1 uses one platform-wide token for every
# restaurant's WhatsApp number — see WhatsAppNumber in app/models.py for
# why no per-restaurant token is ever stored in the database.
WHATSAPP_ACCESS_TOKEN = os.getenv("WHATSAPP_ACCESS_TOKEN", "").strip()

# Graph API version to call, e.g. "v21.0". Kept configurable so a future
# Meta API version bump is a one-line env change, not a code change.
WHATSAPP_API_VERSION = os.getenv("WHATSAPP_API_VERSION", "v21.0").strip()

# --- Widget CORS (Stage 4 Phase D) ---
# A single platform-level trusted origin — e.g. wherever the admin
# dashboard is hosted — always permitted against ANY restaurant's
# widget_key, regardless of that restaurant's own configured allowed
# origins (see app/widget_cors.py). This is orthogonal to, not a
# substitute for, the admin key an operator already needs to preview
# their own widget — it's not a per-restaurant grant.
#
# Currently inert: nothing in this codebase yet makes a cross-origin
# call from this origin (there is no admin-dashboard "preview my
# widget" feature yet — a later phase's concern). Included now so a
# future phase doesn't need to touch app/widget_cors.py again just to
# add it. Optional — leave unset until it's actually needed.
WIDGET_PREVIEW_ORIGIN = os.getenv("WIDGET_PREVIEW_ORIGIN", "").strip()

# --- Booking confirmation email (Production Readiness: notifications) ---
# All optional — like the WhatsApp block above, this is opt-in: leaving
# RESEND_API_KEY/EMAIL_FROM unset simply means no confirmation email is
# ever sent (see app/booking_notifications.py), the same "silently
# inert, never a startup error" behaviour as an unconfigured WhatsApp
# number. Booking creation itself never depends on this being
# configured. Sent via Resend's HTTPS API (app/email_client.py) — no
# SMTP credentials involved.
RESEND_API_KEY = os.getenv("RESEND_API_KEY", "").strip()

# The "From" address confirmation emails are sent as. Required (alongside
# RESEND_API_KEY) for the feature to be considered configured — see
# app/email_client.py's is_email_configured().
EMAIL_FROM = os.getenv("EMAIL_FROM", "").strip()

# --- Error tracking (Sentry) ---
# Fully optional and off by default: leaving SENTRY_DSN unset means
# app/monitoring.py never calls sentry_sdk.init() at all, so nothing is
# collected and nothing is sent anywhere — see that module for the full
# privacy/scrubbing design. Set this to a real Sentry project DSN to
# turn on error tracking in any environment (local, Docker, production).
SENTRY_DSN = os.getenv("SENTRY_DSN", "").strip()

# Free-text label shown in the Sentry UI to tell environments apart
# (e.g. "production", "staging"). Purely cosmetic — never affects what
# is or isn't captured.
SENTRY_ENVIRONMENT = os.getenv("SENTRY_ENVIRONMENT", "production").strip()

# Fraction (0.0-1.0) of requests to sample for Sentry's performance
# tracing feature — separate from error capture, which always happens
# regardless of this value. Defaults to 0.0 (tracing off) because this
# task is about error tracking, not APM; raise it deliberately later if
# performance tracing is ever wanted.
try:
    SENTRY_TRACES_SAMPLE_RATE = float(os.getenv("SENTRY_TRACES_SAMPLE_RATE", "0.0"))
except ValueError:
    SENTRY_TRACES_SAMPLE_RATE = 0.0

# --- API docs gating (Security & Production Hardening Audit finding
# D1) ---
# /docs, /redoc, and /openapi.json are FastAPI's own default interactive
# documentation — reachable by anyone, unauthenticated, with no code
# change at all. This project's own README explicitly recommends /docs
# for local interactive testing and even for the Docker walkthrough
# ("try /docs"), so it stays ENABLED by default — turning it off
# out-of-the-box would break that already-documented workflow. Set
# DISABLE_DOCS=true in a real production environment (e.g. Railway) to
# turn it off there specifically.
#
# There is no other "development vs. production" flag anywhere in this
# codebase to key off instead — SENTRY_ENVIRONMENT above is a free-text
# label for Sentry's UI only, explicitly documented as cosmetic and
# never read anywhere else — so this is intentionally its own small,
# single-purpose switch rather than introducing a broader environment
# concept this project doesn't otherwise have.
DISABLE_DOCS = os.getenv("DISABLE_DOCS", "").strip().lower() in ("1", "true", "yes")


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

    if FORWARDED_ALLOW_IPS.strip() == "*":
        # Not a hard failure — this may be a deliberate, informed choice
        # on some topologies — but "*" means uvicorn's ProxyHeadersMiddleware
        # trusts the FIRST (leftmost) entry in X-Forwarded-For, which on a
        # multi-hop path can be whatever the original, unauthenticated
        # client itself claimed, not a value any proxy actually vouched
        # for. See this file's FORWARDED_ALLOW_IPS comment above.
        print(
            "WARNING: FORWARDED_ALLOW_IPS is set to '*', which trusts ANY "
            "X-Forwarded-For value an inbound request supplies, including one "
            "an attacker sets directly if your app is ever reachable other "
            "than through your trusted proxy. Prefer listing your proxy's "
            "actual IP address(es) instead."
        )
    elif not FORWARDED_ALLOW_IPS:
        print(
            "NOTE: FORWARDED_ALLOW_IPS is not set (uvicorn defaults to trusting "
            "only 127.0.0.1). If this app runs behind a reverse proxy that isn't "
            "on the same host (e.g. Railway's edge), request.client.host — used "
            "for rate limiting (app/rate_limit.py) and failed-admin-auth logging "
            "(app/auth.py) — will reflect the proxy's address, not the real "
            "visitor's, until FORWARDED_ALLOW_IPS is set to that proxy's actual "
            "IP address(es). See backend/.env.example."
        )

    if problems:
        print("\n" + "=" * 70)
        print("STARTUP FAILED — configuration problem(s) found:")
        print("=" * 70)
        for i, problem in enumerate(problems, start=1):
            print(f"\n{i}. {problem}")
        print("\n" + "=" * 70 + "\n")
        sys.exit(1)
