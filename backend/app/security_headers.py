"""
Baseline security response headers (Security & Production Hardening
Audit finding D1).

Applied to EVERY response from this app — registered in app/main.py
AFTER widget_cors_middleware, so it becomes the new OUTERMOST layer
(Starlette/ASGI middleware runs in reverse registration order — see
app/widget_cors.py's own module docstring for why that module cares
about this same ordering). Being outermost means these headers still
land on a response widget_cors_middleware answers directly without ever
reaching a route handler (an OPTIONS preflight, or a 403/404 CORS
decision) — not just on ordinary route responses.

Headers, and why each one:
  - X-Content-Type-Options: nosniff — stops a browser from
    "helpfully" re-interpreting a response's Content-Type (e.g. trying
    to sniff a JSON error body as HTML/script) based on its content
    rather than the declared type.
  - Referrer-Policy: strict-origin-when-cross-origin — avoids leaking a
    full URL path (which could include a widget_key, restaurant_id, or
    other path segment) to a cross-origin destination via the Referer
    header on an outbound navigation/request, while still sending the
    full referrer for a same-origin one.
  - X-Frame-Options: DENY — nothing this app serves is meant to be
    embedded in a frame; most relevant for FastAPI's own /docs and
    /redoc pages (real HTML, otherwise unauthenticated) — see
    app/config.py's DISABLE_DOCS for gating those directly in
    production instead of relying on this alone.
  - Strict-Transport-Security — sent ONLY when the request's own scheme
    is already "https" (never over plain HTTP, where browsers ignore it
    anyway and sending it would be misleading about the connection that
    was actually made). Behind this app's real deployment (Railway's
    proxy), request.url.scheme only reflects the true original scheme
    once FORWARDED_ALLOW_IPS is configured — see app/config.py's own
    comment and tests/test_trusted_proxy.py from the prior (C1)
    hardening pass; until then this header is simply never sent, which
    is safe (no HSTS claimed for a connection that wasn't actually
    HTTPS end-to-end) rather than incorrect.

Deliberately NOT a Content-Security-Policy: a real CSP has to account
for FastAPI's own /docs and /redoc pages (which load Swagger UI/ReDoc
assets from a CDN) and this project's admin dashboard (a separate
static site on Vercel, not served by this backend at all) — getting
that right is a separate, more involved task, out of scope for this
minimal pass.
"""

from starlette.requests import Request
from starlette.responses import Response

_HSTS_VALUE = "max-age=63072000; includeSubDomains"


async def security_headers_middleware(request: Request, call_next) -> Response:
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["X-Frame-Options"] = "DENY"
    if request.url.scheme == "https":
        response.headers["Strict-Transport-Security"] = _HSTS_VALUE
    return response
