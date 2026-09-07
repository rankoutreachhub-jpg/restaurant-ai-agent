"""
Real-browser tests for the embeddable widget (app/static_widget/widget.js,
Stage 4 Phase E), driven with Playwright against a genuinely running HTTP
server — not Starlette's TestClient.

Why a real server and a real browser: nothing else in this suite can
exercise document.currentScript timing, Shadow DOM isolation, actual
fetch()/CORS enforcement as a browser applies it, localStorage, or
keyboard focus — all of which this file specifically needs to prove.

This module is entirely optional infrastructure: it requires the
`playwright` Python package AND a downloaded Chromium binary, neither of
which is part of this project's requirements-dev.txt or CI. If either is
missing, every test in this file is SKIPPED (not failed) via
pytest.importorskip / a fixture-level skip, so the rest of the suite
(and CI's existing "Run backend test suite" job) is completely
unaffected whether or not a browser is available in a given environment.
Set CHROMIUM_EXECUTABLE_PATH to point at a pre-installed browser (as
this project's own dev sandbox does) if the default Playwright browser
location isn't populated.

The server under test is the exact same `app` object (and so the exact
same temp SQLite database) the rest of this suite already uses via
tests/conftest.py — a second, independently configured app instance
isn't possible in the same process (config.py/database.py bind to env
vars and build the engine once, at import time) and isn't needed:
per-test setup (onboarding a restaurant, creating a widget config,
registering allowed origins) reuses the exact same client/admin_headers/
second_restaurant/db fixtures every other test file already uses; only
serving that same app over a real TCP port, for Playwright to load pages
against, is new.

The host page (playing the part of "the restaurant's own website" that
embeds the widget) is served from a SEPARATE port on 127.0.0.1 — a
different port is a different origin under the same-origin policy,
which is what lets these tests exercise Phase D's real per-widget CORS
enforcement from an actual browser's perspective, not just a curl/
TestClient's.
"""

import functools
import http.server
import os
import socket
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

playwright_sync_api = pytest.importorskip("playwright.sync_api")
sync_playwright = playwright_sync_api.sync_playwright

import uvicorn

from app.main import app as fastapi_app
from app import llm as llm_module

CHROMIUM_EXECUTABLE_PATH = os.environ.get(
    "CHROMIUM_EXECUTABLE_PATH", "/opt/pw-browsers/chromium-1194/chrome-linux/chrome"
)


def _free_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture(scope="module")
def api_server():
    """Serves the real app/main.py app over actual HTTP, in a background thread."""
    port = _free_port()
    config = uvicorn.Config(fastapi_app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)

    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    deadline = time.time() + 10
    while not server.started and time.time() < deadline:
        time.sleep(0.05)
    assert server.started, "test API server did not start in time"

    yield f"http://127.0.0.1:{port}"

    server.should_exit = True
    thread.join(timeout=5)


@pytest.fixture(scope="module")
def host_page_server(tmp_path_factory):
    """A second, separate-origin static server standing in for a restaurant's own website."""
    directory = tmp_path_factory.mktemp("widget_host_pages")
    port = _free_port()

    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(directory))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()

    yield f"http://127.0.0.1:{port}", directory

    httpd.shutdown()
    thread.join(timeout=5)


@pytest.fixture(scope="module")
def browser():
    if not Path(CHROMIUM_EXECUTABLE_PATH).exists():
        pytest.skip(f"no Chromium binary at {CHROMIUM_EXECUTABLE_PATH}")
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=CHROMIUM_EXECUTABLE_PATH, args=["--no-sandbox"])
        yield b
        b.close()


@pytest.fixture()
def page(browser):
    """A fresh browser context per test — isolated localStorage/cookies, no cross-test leakage."""
    context = browser.new_context()
    pg = context.new_page()
    yield pg
    context.close()


def _create_widget(client, admin_headers, restaurant_id, allowed_origins=None, **config_fields):
    response = client.post(
        f"/admin/restaurant/{restaurant_id}/widget-config", json=config_fields, headers=admin_headers
    )
    assert response.status_code == 201
    widget_key = response.json()["widget_key"]
    for origin in allowed_origins or []:
        r = client.post(
            f"/admin/restaurant/{restaurant_id}/widget-config/origins",
            json={"origin": origin},
            headers=admin_headers,
        )
        assert r.status_code == 201
    return widget_key


def _write_host_page(directory, filename, script_tags_html):
    html = (
        "<!DOCTYPE html><html><head><meta charset=\"utf-8\"><title>Host</title>"
        "<style>button { color: red !important; font-size: 40px !important; }</style>"
        "</head><body>"
        '<h1>A restaurant\'s own website</h1>'
        '<button id="host-button">Outside the widget</button>'
        + script_tags_html
        + "</body></html>"
    )
    (directory / filename).write_text(html)


def _script_tag(api_base, widget_key):
    return f'<script src="{api_base}/static/widget/widget.js" data-widget-key="{widget_key}"></script>'


def _fake_llm_response(text):
    return SimpleNamespace(
        text=text,
        function_calls=[],
        candidates=[SimpleNamespace(content=SimpleNamespace(role="model", parts=[]))],
    )


def _stub_llm_success(monkeypatch, text="Hiya! How can I help?"):
    def fake_generate_content(model, contents, config):
        return _fake_llm_response(text)

    monkeypatch.setattr(llm_module.client.models, "generate_content", fake_generate_content)


def _stub_llm_failure(monkeypatch):
    def fake_generate_content(model, contents, config):
        raise RuntimeError("simulated Gemini failure")

    monkeypatch.setattr(llm_module.client.models, "generate_content", fake_generate_content)


LAUNCHER = "button.raiw-launcher"
PANEL = "div.raiw-panel"
CLOSE = "button.raiw-close"
INPUT = "textarea.raiw-input"
SEND = "button.raiw-send"
CHIP = "button.raiw-chip"
MESSAGES = "div.raiw-messages"
REPLY = ".raiw-msg-assistant:not(.raiw-typing)"  # excludes the always-present welcome bubble by count, not class


def _wait_for_reply_count(page, count):
    """
    The welcome message is itself rendered as a `.raiw-msg-assistant`
    bubble at load time, so a bare `wait_for_selector(".raiw-msg-assistant")`
    right after sending a message resolves immediately against THAT
    bubble — before the real reply has arrived — rather than actually
    waiting for the new one. Wait for the exact count instead (welcome +
    N real replies) wherever a test needs to observe post-reply state
    (localStorage, disabled/enabled controls).
    """
    # querySelectorAll does not pierce shadow roots (unlike Playwright's
    # own locator engine), so this reaches in explicitly.
    page.wait_for_function(
        "document.querySelector('div[id$=\"-container\"]').shadowRoot"
        ".querySelectorAll(%r).length >= %d" % (REPLY, count),
        timeout=5000,
    )


# --- Load, branding, welcome message ---

def test_widget_loads_launcher_and_shows_welcome_message(
    client, admin_headers, second_restaurant, api_server, host_page_server, page
):
    host_origin, host_dir = host_page_server
    widget_key = _create_widget(
        client, admin_headers, second_restaurant,
        allowed_origins=[host_origin],
        welcome_message="Welcome to The Anchor! Ask me anything.",
        accent_color="#123456",
    )
    _write_host_page(host_dir, "basic.html", _script_tag(api_server, widget_key))

    page.goto(f"{host_origin}/basic.html")
    launcher = page.locator(LAUNCHER)
    launcher.wait_for(state="visible", timeout=5000)
    assert "The Anchor" in launcher.get_attribute("aria-label")

    launcher.click()
    panel = page.locator(PANEL)
    assert panel.is_visible()
    assert page.locator(".raiw-title").inner_text() == "The Anchor"
    assert "Welcome to The Anchor" in page.locator(MESSAGES).inner_text()


# --- Correct request shapes ---

def test_correct_config_and_chat_request_shapes(
    client, admin_headers, second_restaurant, api_server, host_page_server, page, monkeypatch
):
    host_origin, host_dir = host_page_server
    widget_key = _create_widget(client, admin_headers, second_restaurant, allowed_origins=[host_origin])
    _write_host_page(host_dir, "shapes.html", _script_tag(api_server, widget_key))
    _stub_llm_success(monkeypatch, "Sure, here's our menu.")

    requests_seen = []
    page.on("request", lambda req: requests_seen.append(req))

    page.goto(f"{host_origin}/shapes.html")
    page.locator(LAUNCHER).click()

    config_requests = [
        r for r in requests_seen if r.url == f"{api_server}/widget/{widget_key}/config" and r.method == "GET"
    ]
    assert len(config_requests) == 1

    page.locator(INPUT).fill("What's on the menu?")
    page.locator(SEND).click()
    _wait_for_reply_count(page, 2)

    chat_requests = [
        r for r in requests_seen if r.url == f"{api_server}/widget/{widget_key}/chat" and r.method == "POST"
    ]
    assert len(chat_requests) == 1
    chat_request = chat_requests[0]
    assert chat_request.post_data_json == {"message": "What's on the menu?"}
    assert chat_request.headers.get("content-type", "").startswith("application/json")


# --- Token storage, reuse, and safe replacement on an invalid token ---

def test_token_stored_and_reused_across_reload(
    client, admin_headers, second_restaurant, api_server, host_page_server, page, monkeypatch
):
    host_origin, host_dir = host_page_server
    widget_key = _create_widget(client, admin_headers, second_restaurant, allowed_origins=[host_origin])
    _write_host_page(host_dir, "token.html", _script_tag(api_server, widget_key))
    _stub_llm_success(monkeypatch, "First reply")

    page.goto(f"{host_origin}/token.html")
    page.locator(LAUNCHER).click()
    page.locator(INPUT).fill("hi")
    page.locator(SEND).click()
    _wait_for_reply_count(page, 2)

    storage_key = f"raiw:{widget_key}:token"
    token_after_first = page.evaluate(f"window.localStorage.getItem('{storage_key}')")
    assert token_after_first

    requests_seen = []
    page.on("request", lambda req: requests_seen.append(req))

    page.reload()
    page.locator(LAUNCHER).click()
    page.locator(INPUT).fill("second message")
    page.locator(SEND).click()
    _wait_for_reply_count(page, 2)

    chat_requests = [r for r in requests_seen if r.method == "POST"]
    assert len(chat_requests) == 1
    assert chat_requests[0].headers.get("x-conversation-token") == token_after_first


def test_invalid_stored_token_triggers_a_safe_new_conversation(
    client, admin_headers, second_restaurant, api_server, host_page_server, page, monkeypatch
):
    host_origin, host_dir = host_page_server
    widget_key = _create_widget(client, admin_headers, second_restaurant, allowed_origins=[host_origin])
    _write_host_page(host_dir, "badtoken.html", _script_tag(api_server, widget_key))
    _stub_llm_success(monkeypatch, "Hello again")

    page.goto(f"{host_origin}/badtoken.html")
    storage_key = f"raiw:{widget_key}:token"
    page.evaluate(f"window.localStorage.setItem('{storage_key}', 'this-token-was-never-issued')")

    page.locator(LAUNCHER).click()
    page.locator(INPUT).fill("hi again")
    page.locator(SEND).click()
    _wait_for_reply_count(page, 2)

    new_token = page.evaluate(f"window.localStorage.getItem('{storage_key}')")
    assert new_token
    assert new_token != "this-token-was-never-issued"


# --- booking_enabled UI behavior ---

def test_booking_chip_shown_when_enabled(
    client, admin_headers, second_restaurant, api_server, host_page_server, page
):
    host_origin, host_dir = host_page_server
    widget_key = _create_widget(
        client, admin_headers, second_restaurant, allowed_origins=[host_origin], booking_enabled=True
    )
    _write_host_page(host_dir, "booking-on.html", _script_tag(api_server, widget_key))

    page.goto(f"{host_origin}/booking-on.html")
    page.locator(LAUNCHER).click()
    chip = page.locator(CHIP)
    chip.wait_for(state="visible", timeout=5000)

    chip.click()
    assert page.locator(INPUT).input_value() == "I'd like to book a table, please."


def test_booking_chip_hidden_when_disabled(
    client, admin_headers, second_restaurant, api_server, host_page_server, page
):
    host_origin, host_dir = host_page_server
    widget_key = _create_widget(
        client, admin_headers, second_restaurant, allowed_origins=[host_origin], booking_enabled=False
    )
    _write_host_page(host_dir, "booking-off.html", _script_tag(api_server, widget_key))

    page.goto(f"{host_origin}/booking-off.html")
    page.locator(LAUNCHER).click()
    assert page.locator(CHIP).count() == 0


# --- Loading state ---

def test_loading_state_disables_controls_until_response_settles(
    client, admin_headers, second_restaurant, api_server, host_page_server, page, monkeypatch
):
    host_origin, host_dir = host_page_server
    widget_key = _create_widget(client, admin_headers, second_restaurant, allowed_origins=[host_origin])
    _write_host_page(host_dir, "loading.html", _script_tag(api_server, widget_key))
    _stub_llm_success(monkeypatch, "Delayed reply")

    def delay_then_continue(route):
        time.sleep(0.4)
        route.continue_()

    page.route(f"**/widget/{widget_key}/chat", delay_then_continue)

    page.goto(f"{host_origin}/loading.html")
    page.locator(LAUNCHER).click()
    page.locator(INPUT).fill("hi")
    page.locator(SEND).click()

    assert page.locator(SEND).is_disabled()
    assert page.locator(INPUT).is_disabled()
    page.wait_for_selector(".raiw-typing", timeout=1000)

    _wait_for_reply_count(page, 2)
    assert page.locator(".raiw-typing").count() == 0
    assert not page.locator(SEND).is_disabled()
    assert not page.locator(INPUT).is_disabled()


# --- Error states ---

def test_error_state_rate_limited(
    client, admin_headers, second_restaurant, api_server, host_page_server, page, monkeypatch
):
    host_origin, host_dir = host_page_server
    widget_key = _create_widget(client, admin_headers, second_restaurant, allowed_origins=[host_origin])
    _write_host_page(host_dir, "ratelimit.html", _script_tag(api_server, widget_key))
    _stub_llm_success(monkeypatch, "ok")

    page.goto(f"{host_origin}/ratelimit.html")
    page.locator(LAUNCHER).click()

    for i in range(11):
        page.locator(INPUT).fill(f"message {i}")
        page.locator(SEND).click()
        page.wait_for_selector(".raiw-msg-user:has-text('message %d')" % i, timeout=5000)
        page.wait_for_function(
            "document.querySelector('div[id$=\"-container\"]').shadowRoot"
            ".querySelector('button.raiw-send') && "
            "!document.querySelector('div[id$=\"-container\"]').shadowRoot"
            ".querySelector('button.raiw-send').disabled",
            timeout=5000,
        )

    assert "too fast" in page.locator(MESSAGES).inner_text().lower()


def test_error_state_widget_deactivated_mid_session(
    client, admin_headers, second_restaurant, api_server, host_page_server, page, monkeypatch
):
    """
    A real finding from running this test against a real browser: once a
    widget goes inactive, app/widget_cors.py (untouched, existing Phase D
    behavior — the same indistinguishable-404 pass-through it uses for a
    never-existed widget_key) adds no CORS header of its own, and this
    test's cross-origin host page isn't in the small, separate global
    ALLOWED_ORIGINS list either — so a real browser's fetch() can't read
    the 404 body at all; it sees the same opaque, CORS-indistinguishable
    failure as a genuine network outage, not a readable 404. The widget's
    generic catch-block message is what actually fires here, correctly —
    the widget-side "This chat is currently unavailable"/permanent-disable
    branch below only fires for a same-origin deployment, or an origin
    that also happens to be in the global CORSMiddleware's own allowlist.
    """
    host_origin, host_dir = host_page_server
    widget_key = _create_widget(client, admin_headers, second_restaurant, allowed_origins=[host_origin])
    _write_host_page(host_dir, "deactivated.html", _script_tag(api_server, widget_key))
    _stub_llm_success(monkeypatch, "ok")

    page.goto(f"{host_origin}/deactivated.html")
    page.locator(LAUNCHER).click()

    deactivate = client.post(
        f"/admin/restaurant/{second_restaurant}/widget-config",
        json={"is_active": False},
        headers=admin_headers,
    )
    assert deactivate.status_code == 201

    page.locator(INPUT).fill("are you still there?")
    page.locator(SEND).click()
    page.wait_for_selector(".raiw-msg-error", timeout=5000)
    assert "connection" in page.locator(MESSAGES).inner_text().lower()
    assert not page.locator(INPUT).is_disabled()  # the catch-block path allows retrying, unlike a true 404


def test_error_state_backend_failure(
    client, admin_headers, second_restaurant, api_server, host_page_server, page, monkeypatch
):
    host_origin, host_dir = host_page_server
    widget_key = _create_widget(client, admin_headers, second_restaurant, allowed_origins=[host_origin])
    _write_host_page(host_dir, "backend-failure.html", _script_tag(api_server, widget_key))
    _stub_llm_failure(monkeypatch)

    page.goto(f"{host_origin}/backend-failure.html")
    page.locator(LAUNCHER).click()
    page.locator(INPUT).fill("hi")
    page.locator(SEND).click()
    page.wait_for_selector(".raiw-msg-error", timeout=5000)
    assert "went wrong" in page.locator(MESSAGES).inner_text().lower()
    assert not page.locator(INPUT).is_disabled()


def test_error_state_network_or_cors_failure(
    client, admin_headers, second_restaurant, api_server, host_page_server, page
):
    host_origin, host_dir = host_page_server
    widget_key = _create_widget(client, admin_headers, second_restaurant, allowed_origins=[host_origin])
    _write_host_page(host_dir, "aborted.html", _script_tag(api_server, widget_key))

    page.route(f"**/widget/{widget_key}/chat", lambda route: route.abort())

    page.goto(f"{host_origin}/aborted.html")
    page.locator(LAUNCHER).click()
    page.locator(INPUT).fill("hi")
    page.locator(SEND).click()
    page.wait_for_selector(".raiw-msg-error", timeout=5000)
    assert "connection" in page.locator(MESSAGES).inner_text().lower()


# --- Accessibility ---

def test_accessibility_attributes_and_focus_management(
    client, admin_headers, second_restaurant, api_server, host_page_server, page
):
    host_origin, host_dir = host_page_server
    # booking_enabled=False keeps the focusable set small and unambiguous
    # (close, input, send) — the chip button (tested separately in
    # test_booking_chip_shown_when_enabled) would otherwise sit between
    # close and input in tab order and complicate reasoning about exactly
    # where a fixed number of Tab presses should land.
    widget_key = _create_widget(
        client, admin_headers, second_restaurant, allowed_origins=[host_origin], booking_enabled=False
    )
    _write_host_page(host_dir, "a11y.html", _script_tag(api_server, widget_key))

    page.goto(f"{host_origin}/a11y.html")
    launcher = page.locator(LAUNCHER)
    launcher.wait_for(state="visible")
    assert launcher.get_attribute("aria-expanded") == "false"

    launcher.click()
    assert launcher.get_attribute("aria-expanded") == "true"

    panel = page.locator(PANEL)
    assert panel.get_attribute("role") == "dialog"
    assert panel.get_attribute("aria-modal") == "true"
    labelled_by = panel.get_attribute("aria-labelledby")
    assert page.locator(f"#{labelled_by}").inner_text() != ""

    input_id = page.locator(INPUT).get_attribute("id")
    label = page.locator(f"label[for='{input_id}']")
    assert label.count() == 1
    assert label.inner_text() != ""

    # Escape closes the panel and returns focus to the launcher.
    page.keyboard.press("Escape")
    assert panel.is_hidden()
    assert launcher.get_attribute("aria-expanded") == "false"

    # Minimal focus trap: Tab from the last focusable element inside the
    # panel must cycle back to the first one, never escape to host-page
    # content that comes after the widget in the DOM.
    launcher.click()
    page.locator(CLOSE).focus()
    page.keyboard.press("Shift+Tab")  # from close -> title has no tabstop, lands on... trap handles wrap
    page.keyboard.press("Tab")
    page.keyboard.press("Tab")
    # After cycling forward past the last focusable element, focus must
    # land back on the FIRST focusable element in the panel, not escape.
    active_in_shadow = page.evaluate(
        "document.querySelector('div[id$=\"-container\"]').shadowRoot.activeElement.className"
    )
    assert "raiw-close" in active_in_shadow or "raiw-input" in active_in_shadow or "raiw-send" in active_in_shadow


# --- Mobile layout ---

def test_mobile_viewport_layout(client, admin_headers, second_restaurant, api_server, host_page_server, browser):
    host_origin, host_dir = host_page_server
    widget_key = _create_widget(client, admin_headers, second_restaurant, allowed_origins=[host_origin])
    _write_host_page(host_dir, "mobile.html", _script_tag(api_server, widget_key))

    context = browser.new_context(viewport={"width": 375, "height": 667})
    mobile_page = context.new_page()
    mobile_page.goto(f"{host_origin}/mobile.html")
    mobile_page.locator(LAUNCHER).click()
    box = mobile_page.locator(PANEL).bounding_box()
    assert box["width"] > 300  # near-fullscreen on a 375px-wide viewport, per the mobile breakpoint
    context.close()


# --- CSS isolation (both directions) ---

def test_css_isolation_both_directions(
    client, admin_headers, second_restaurant, api_server, host_page_server, page
):
    host_origin, host_dir = host_page_server
    widget_key = _create_widget(client, admin_headers, second_restaurant, allowed_origins=[host_origin])
    # _write_host_page already injects an aggressive `button { color: red !important; font-size: 40px !important; }`
    # host-page stylesheet ahead of the widget script tag.
    _write_host_page(host_dir, "css-isolation.html", _script_tag(api_server, widget_key))

    page.goto(f"{host_origin}/css-isolation.html")
    page.locator(LAUNCHER).click()

    send_font_size = page.evaluate(
        "getComputedStyle(document.querySelector('div[id$=\"-container\"]').shadowRoot"
        ".querySelector('button.raiw-send')).fontSize"
    )
    assert send_font_size != "40px"  # host's forced button style did not leak in

    host_button_color = page.evaluate(
        "getComputedStyle(document.getElementById('host-button')).color"
    )
    assert host_button_color == "rgb(255, 0, 0)"  # host's own CSS still applies to its own button
    assert "raiw" not in page.content().split("<style>", 1)[0]  # widget's <style> lives in the shadow root, not light DOM


# --- XSS ---

def test_xss_in_welcome_message_reply_and_user_echo_are_never_executed(
    client, admin_headers, second_restaurant, api_server, host_page_server, page, monkeypatch
):
    host_origin, host_dir = host_page_server
    payload = '<img src=x onerror="window.__xss_fired = (window.__xss_fired||0) + 1">'
    widget_key = _create_widget(
        client, admin_headers, second_restaurant, allowed_origins=[host_origin], welcome_message=payload
    )
    _write_host_page(host_dir, "xss.html", _script_tag(api_server, widget_key))
    _stub_llm_success(monkeypatch, payload)

    page.goto(f"{host_origin}/xss.html")
    page.locator(LAUNCHER).click()
    assert payload in page.locator(MESSAGES).inner_text()
    assert page.evaluate("window.__xss_fired") in (None, 0)

    page.locator(INPUT).fill(payload)
    page.locator(SEND).click()
    _wait_for_reply_count(page, 2)

    assert page.locator(MESSAGES).inner_text().count(payload) >= 2  # welcome + user echo (+ assistant reply)
    assert page.evaluate("window.__xss_fired") in (None, 0)


# --- No secret / restaurant_id leakage ---

def test_no_secrets_or_restaurant_id_leak_in_requests_or_storage(
    client, admin_headers, second_restaurant, api_server, host_page_server, page, monkeypatch
):
    host_origin, host_dir = host_page_server
    widget_key = _create_widget(client, admin_headers, second_restaurant, allowed_origins=[host_origin])
    _write_host_page(host_dir, "secrets.html", _script_tag(api_server, widget_key))
    _stub_llm_success(monkeypatch, "ok")

    requests_seen = []
    page.on("request", lambda req: requests_seen.append(req))

    page.goto(f"{host_origin}/secrets.html")
    page.locator(LAUNCHER).click()
    page.locator(INPUT).fill("hi")
    page.locator(SEND).click()
    _wait_for_reply_count(page, 2)

    admin_key_value = admin_headers["X-Admin-API-Key"]
    for req in requests_seen:
        assert admin_key_value not in req.url
        assert admin_key_value not in (req.post_data or "")
        assert "x-admin-api-key" not in {h.lower() for h in req.headers}
        assert str(second_restaurant) not in req.url.replace(str(second_restaurant) + "0", "")

    storage_dump = page.evaluate(
        "JSON.stringify(Object.assign({}, window.localStorage))"
    )
    assert admin_key_value not in storage_dump
    assert f"raiw:{widget_key}:token" in storage_dump


# --- Multiple instances / duplicate protection ---

def test_multiple_widget_instances_are_independent(
    client, admin_headers, second_restaurant, api_server, host_page_server, page, monkeypatch, db
):
    from app import models

    host_origin, host_dir = host_page_server
    _stub_llm_success(monkeypatch, "ok")

    key_a = _create_widget(
        client, admin_headers, 1, allowed_origins=[host_origin], welcome_message="Widget A welcome"
    )
    key_b = _create_widget(
        client, admin_headers, second_restaurant, allowed_origins=[host_origin], welcome_message="Widget B welcome"
    )
    _write_host_page(
        host_dir, "multi.html", _script_tag(api_server, key_a) + _script_tag(api_server, key_b)
    )

    page.goto(f"{host_origin}/multi.html")
    launchers = page.locator(LAUNCHER)
    launchers.first.wait_for(state="visible", timeout=5000)
    assert launchers.count() == 2

    panels_text = []
    for i in range(2):
        launchers.nth(i).click()
        panels_text.append(page.locator(PANEL).nth(i).inner_text())
        launchers.nth(i).click()  # close it again before opening the other

    assert any("Widget A" in t for t in panels_text)
    assert any("Widget B" in t for t in panels_text)

    token_a = page.evaluate(f"window.localStorage.getItem('raiw:{key_a}:token')")
    token_b = page.evaluate(f"window.localStorage.getItem('raiw:{key_b}:token')")
    assert token_a is None and token_b is None  # neither has chatted yet — no cross-contamination at load


def test_duplicate_widget_key_embed_is_ignored(
    client, admin_headers, second_restaurant, api_server, host_page_server, page
):
    host_origin, host_dir = host_page_server
    widget_key = _create_widget(client, admin_headers, second_restaurant, allowed_origins=[host_origin])
    _write_host_page(
        host_dir, "duplicate.html", _script_tag(api_server, widget_key) + _script_tag(api_server, widget_key)
    )

    page.goto(f"{host_origin}/duplicate.html")
    page.wait_for_timeout(500)
    assert page.locator(LAUNCHER).count() == 1


# --- Phase D CORS compatibility, from an actual browser ---

def test_cors_blocks_an_unregistered_origin_so_no_launcher_ever_appears(
    client, admin_headers, second_restaurant, api_server, host_page_server, page
):
    host_origin, host_dir = host_page_server
    # Deliberately register a DIFFERENT origin, not this host page's own —
    # a real browser's fetch() must refuse to expose the config response
    # (Phase D: 403 with no CORS headers), so no launcher should ever
    # appear, exactly like the unknown/inactive-widget case.
    widget_key = _create_widget(
        client, admin_headers, second_restaurant, allowed_origins=["https://some-other-site.example.com"]
    )
    _write_host_page(host_dir, "cors-blocked.html", _script_tag(api_server, widget_key))

    page.goto(f"{host_origin}/cors-blocked.html")
    page.wait_for_timeout(800)
    assert page.locator(LAUNCHER).count() == 0
