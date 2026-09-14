"""
Real-browser tests for the Paddle Checkout integration on
frontend/pricing.html (see tests/test_frontend_static_pages.py for the
plain content/string-based checks on the same page -- this file instead
verifies the actual onclick wiring executes correctly in a real browser).

frontend/pricing.html has no backend dependency at all (unlike
admin.html), so this serves the frontend/ directory directly over a
plain HTTP server -- no FastAPI app/test database involved.

paddle.js loads from an external CDN (cdn.paddle.com) that this sandbox
environment cannot reach, so every test here deliberately blocks that
network request via page.route(...) rather than relying on it
incidentally succeeding or failing -- deterministic either way, and
exactly mirrors what a real visitor with an ad-blocker would see.

The live Paddle client-side token is supplied at runtime by a Vercel
serverless function (frontend/api/paddle-config.js), not by a plain
static file, so it cannot be served by the plain HTTP server this
module spins up for frontend/. The `page` fixture blocks that endpoint
by default (mirroring "no live token configured yet"); tests that need
a working checkout explicitly stub it with a fake token via
_stub_paddle_runtime_config(page).

Optional infrastructure: skipped entirely if playwright/Chromium aren't
available, same convention as tests/test_widget_browser.py.
"""

import functools
import http.server
import os
import socket
import threading
from pathlib import Path

import pytest

playwright_sync_api = pytest.importorskip("playwright.sync_api")
sync_playwright = playwright_sync_api.sync_playwright

CHROMIUM_EXECUTABLE_PATH = os.environ.get(
    "CHROMIUM_EXECUTABLE_PATH", "/opt/pw-browsers/chromium-1194/chrome-linux/chrome"
)
FRONTEND_DIR = Path(__file__).resolve().parent.parent.parent / "frontend"

# Injected before any page script runs (Playwright guarantees this on
# every navigation) -- records every Paddle.Checkout.open(...) call
# instead of a real Paddle SDK, which the real <script src="https://
# cdn.paddle.com/...">  tag on the page can never load in this sandbox
# (see module docstring) and which page.route() below blocks explicitly
# anyway, so the real tag can never overwrite this stub mid-load.
_PADDLE_STUB = """
    window.__paddleCalls = [];
    window.Paddle = {
        Initialize: function(opts) { window.__paddleInitToken = opts.token; },
        Checkout: { open: function(opts) { window.__paddleCalls.push(opts); } }
    };
"""


_FAKE_LIVE_TOKEN_FOR_TESTS = "test_stub_paddle_client_token"


def _stub_paddle_runtime_config(page, token=_FAKE_LIVE_TOKEN_FOR_TESTS):
    """Overrides the `page` fixture's default block of /api/paddle-config.js
    with a fake token, standing in for the real Vercel serverless
    function/env var in production."""
    page.route(
        "**/api/paddle-config.js",
        lambda route: route.fulfill(
            status=200,
            content_type="application/javascript",
            body=f"window.PADDLE_CLIENT_TOKEN = {token!r};",
        ),
    )


def _free_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture(scope="module")
def pricing_page_server():
    port = _free_port()
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(FRONTEND_DIR))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{port}"
    server.shutdown()
    thread.join(timeout=5)


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=CHROMIUM_EXECUTABLE_PATH)
        yield b
        b.close()


@pytest.fixture()
def page(browser):
    pg = browser.new_page()
    pg.route("https://cdn.paddle.com/**", lambda route: route.abort())
    # /api/paddle-config.js is a Vercel serverless function in production,
    # not a static file the plain HTTP server above can serve -- block it
    # by default (mirroring "no live token configured"); tests that need
    # checkout to actually work call _stub_paddle_runtime_config(page).
    pg.route("**/api/paddle-config.js", lambda route: route.abort())
    yield pg
    pg.close()


def test_clicking_a_plan_button_calls_paddle_checkout_with_that_plans_own_price_id(page, pricing_page_server):
    page.add_init_script(_PADDLE_STUB)
    _stub_paddle_runtime_config(page)
    page.goto(f"{pricing_page_server}/pricing.html")

    page.click("button[data-plan='growth']")
    calls = page.evaluate("window.__paddleCalls")
    assert len(calls) == 1
    assert calls[0]["items"] == [{"priceId": page.evaluate("PADDLE_PRICE_IDS.growth"), "quantity": 1}]
    assert calls[0]["settings"]["displayMode"] == "overlay"
    assert calls[0]["settings"]["successUrl"] == "https://jantarai.com/checkout-success.html"


def test_each_plan_button_uses_its_own_distinct_price_id(page, pricing_page_server):
    page.add_init_script(_PADDLE_STUB)
    _stub_paddle_runtime_config(page)
    page.goto(f"{pricing_page_server}/pricing.html")

    for plan in ("starter", "growth", "pro"):
        page.click(f"button[data-plan='{plan}']")
    calls = page.evaluate("window.__paddleCalls")
    price_ids = [c["items"][0]["priceId"] for c in calls]
    assert len(price_ids) == 3
    assert len(set(price_ids)) == 3, "each plan must check out against its own distinct Paddle price id"


def test_paddle_initialize_is_called_with_the_client_token_before_any_checkout(page, pricing_page_server):
    page.add_init_script(_PADDLE_STUB)
    _stub_paddle_runtime_config(page)
    page.goto(f"{pricing_page_server}/pricing.html")

    init_token = page.evaluate("window.__paddleInitToken")
    expected_token = page.evaluate("PADDLE_CLIENT_TOKEN")
    assert init_token == expected_token
    assert init_token == _FAKE_LIVE_TOKEN_FOR_TESTS


def test_graceful_fallback_shown_when_paddle_fails_to_load(page, pricing_page_server):
    # No stub injected here -- with the real cdn.paddle.com request
    # blocked (see the `page` fixture) and nothing replacing it,
    # window.Paddle never exists, exactly like a real visitor whose
    # ad-blocker stripped the script.
    page.goto(f"{pricing_page_server}/pricing.html")

    error_el = page.locator("#checkout-error")
    assert not error_el.is_visible()
    page.click("button[data-plan='starter']")
    assert error_el.is_visible()
    assert "rankoutreachhub@gmail.com" in error_el.inner_text()
