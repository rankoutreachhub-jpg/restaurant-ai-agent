"""
Production error tracking (Sentry) — Production Readiness Audit BLOCKER #1.

Fully optional and disabled by construction: init_sentry() does nothing
at all unless config.SENTRY_DSN is set, so a deployment that never
configures it behaves exactly as before this module existed — no import
side effects, no network calls, no captured data, nothing.

What this deliberately does NOT rely on to stay safe: it does not trust
that every future call site remembers to scrub its own data before
reporting an error. Instead, safety comes from three independent layers,
so a mistake in one still leaves the others in place:

  1. Configuration that disables the riskiest data sources outright:
       - include_local_variables=False — the single most important
         setting here. Sentry's default behaviour attaches every local
         variable from every stack frame to a captured exception. The
         functions that catch and report errors in this app
         (routers/chat.py, routers/widget.py, whatsapp_processing.py)
         have raw customer message text, phone numbers, and Gemini
         replies sitting in local variables at exactly the point an
         exception would be caught — with this off, none of that is
         ever attached, regardless of what the code does.
       - send_default_pii=False — never attach request IP, cookies, or
         a "user" object.
       - max_request_body_size="never" — never attach a request body
         (which could contain a chat message, a booking's customer
         name/phone/email, or a WhatsApp webhook payload) to any event.

  2. before_send / before_breadcrumb hooks (defense in depth on top of
     #1): strip every request header (this app's own auth headers —
     X-Admin-API-Key, X-Hub-Signature-256, X-Conversation-Token — are
     not "well-known" secret header names Sentry's own PII scrubbing
     would necessarily recognise, so they are removed outright rather
     than trusted to an allowlist/denylist maintained elsewhere);
     strip any request body/query string/cookies that slipped through;
     strip query strings from breadcrumb URLs (outgoing calls to the
     Gemini and WhatsApp Graph APIs are exactly the kind of HTTP client
     activity Sentry auto-instruments as breadcrumbs, and either API
     could in principle carry a credential in a URL rather than a
     header); redact the message text of exception types already known
     to embed raw input in `str(exception)` itself (see
     _SCRUBBED_EXCEPTION_TYPES below) — this survives even though #1's
     include_local_variables=False only ever protected *local
     variables*, not an exception's own message string.

  3. Existing, unrelated-to-Sentry logging discipline: this app's
     logger.exception(...)/logger.warning(...) calls (see
     app/logging_config.py's own documented invariant) already never
     include message content, customer PII, or secrets — only fixed
     operational strings plus IDs. Sentry's default LoggingIntegration
     turns any ERROR-level log record into a Sentry event automatically,
     which is what actually captures the three existing generic
     `except Exception:` sites in chat.py/widget.py/
     whatsapp_processing.py — no call to this module was needed at
     those three sites, and none was added, specifically so nothing
     about their existing, already-audited behaviour changes.

Together with Sentry's own default Starlette/FastAPI integration (auto-
enabled because those packages are installed — no explicit integration
list needed), this also captures any genuinely unhandled exception from
every OTHER router (admin.py, bookings.py, platform_admin.py,
conversations.py) that has no explicit try/except of its own today.
"""

import logging

import sentry_sdk

from . import config

logger = logging.getLogger(__name__)

# Exception types whose own message text is known to embed raw,
# unsanitized input (see app/phone.py) — reduced to a generic message on
# the way out. This does NOT hide the exception from Sentry (the type
# name, stack trace location, and event still appear); it only redacts
# the *text* of that one message, which is the one place
# include_local_variables=False cannot protect (it isn't a local
# variable, it's the exception's own constructed string).
_SCRUBBED_EXCEPTION_TYPES = {"InvalidPhoneNumberError"}

_REDACTED = "[redacted]"


def _strip_query_string(url):
    if not isinstance(url, str) or "?" not in url:
        return url
    return url.split("?", 1)[0] + "?" + _REDACTED


def _scrub_request(request_data):
    if not isinstance(request_data, dict):
        return request_data
    # Headers are removed entirely rather than filtered by name — this
    # app's own auth headers are not standard ones Sentry's built-in
    # scrubbing would necessarily recognise.
    request_data.pop("headers", None)
    request_data.pop("data", None)
    request_data.pop("cookies", None)
    request_data.pop("query_string", None)
    if "url" in request_data:
        request_data["url"] = _strip_query_string(request_data["url"])
    return request_data


def _scrub_exception_values(exception_data):
    if not isinstance(exception_data, dict):
        return
    for value in exception_data.get("values", []) or []:
        if isinstance(value, dict) and value.get("type") in _SCRUBBED_EXCEPTION_TYPES:
            value["value"] = _REDACTED


def before_send(event, hint):
    """
    Runs on every event right before it would be sent. Never raises —
    an error here must never itself crash request handling, so any
    unexpected shape in `event` is skipped over rather than failing.
    """
    try:
        if "request" in event:
            event["request"] = _scrub_request(event["request"])
        _scrub_exception_values(event.get("exception"))
    except Exception:
        logger.warning("Sentry before_send scrubbing failed; dropping event")
        return None
    return event


def before_breadcrumb(breadcrumb, hint):
    """
    Outgoing HTTP calls (Gemini, WhatsApp Graph API) are auto-recorded
    as breadcrumbs by Sentry's HTTP client integrations — this strips
    any query string from a breadcrumb's URL, in case either API's
    client library carries a credential there rather than in a header.
    """
    try:
        data = breadcrumb.get("data")
        if isinstance(data, dict) and "url" in data:
            data["url"] = _strip_query_string(data["url"])
    except Exception:
        logger.warning("Sentry before_breadcrumb scrubbing failed; dropping breadcrumb")
        return None
    return breadcrumb


def init_sentry(transport=None) -> None:
    """
    Call once, at app startup. A no-op — no import side effects, no
    network calls, nothing configured or sent anywhere — unless
    config.SENTRY_DSN is set, so leaving it unset is a fully supported,
    harmless way to run with error tracking off.

    `transport` is never passed by app/main.py's real call (it defaults
    to None, which makes sentry_sdk use its normal HTTPS transport) —
    it exists solely so tests can inject an in-memory fake transport
    and verify capture behaviour without making a real network call.
    """
    if not config.SENTRY_DSN:
        logger.info("Sentry DSN not configured; error tracking is disabled.")
        return

    sentry_sdk.init(
        dsn=config.SENTRY_DSN,
        environment=config.SENTRY_ENVIRONMENT,
        traces_sample_rate=config.SENTRY_TRACES_SAMPLE_RATE,
        send_default_pii=False,
        include_local_variables=False,
        max_request_body_size="never",
        before_send=before_send,
        before_breadcrumb=before_breadcrumb,
        transport=transport,
    )
    logger.info("Sentry error tracking enabled (environment=%s).", config.SENTRY_ENVIRONMENT)
