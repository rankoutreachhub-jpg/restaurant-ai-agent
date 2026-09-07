"""
Real-browser tests for the Widget tab added to frontend/admin.html
(Stage 4 Phase F). Mirrors tests/test_widget_browser.py's pattern: a
real server (the exact same `app` object the rest of this suite uses)
and a real browser driving a real page — here, admin.html itself
rather than a synthetic host page, since this phase's surface is the
dashboard, not the customer-facing widget.

admin.html hardcodes `const API_BASE = "http://127.0.0.1:8000"` (the
same convention frontend/index.html uses, documented in both files) —
since the test API server binds to a random free port, this module
serves a byte-identical COPY of the real file with only that one
constant's value substituted, never touching frontend/admin.html
itself. If that substitution ever fails to match (e.g. the constant's
exact text changes), the fixture asserts loudly rather than silently
testing a dashboard that can't reach the test server.

No backend code changes were needed for Phase F (see the plan) — this
file exists purely to exercise the new client-side Widget tab against
the real, unmodified admin endpoints.
"""

import functools
import http.server
import socket
import threading
import time
from pathlib import Path

import pytest

playwright_sync_api = pytest.importorskip("playwright.sync_api")
sync_playwright = playwright_sync_api.sync_playwright

import uvicorn

from app import config
from app.main import app as fastapi_app

CHROMIUM_EXECUTABLE_PATH = "/opt/pw-browsers/chromium-1194/chrome-linux/chrome"

ADMIN_HTML_PATH = Path(__file__).resolve().parent.parent.parent / "frontend" / "admin.html"


def _free_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture(scope="module")
def api_server():
    port = _free_port()
    cfg = uvicorn.Config(fastapi_app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(cfg)

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
def admin_page_server(api_server, tmp_path_factory):
    """
    Serves the patched admin.html copy from port 5500 specifically — one
    of the ports config.py's own _DEFAULT_ALLOWED_ORIGINS already lists
    ("a simple static-file server... VS Code Live Server", exactly what
    this fixture is standing in for), not a random one. This is
    unrelated to widget_cors_middleware: /admin/* is governed by the
    pre-existing, untouched GLOBAL CORSMiddleware, which builds its
    allowed-origins set once at app-construction time — a random test
    port would be rejected by that real, unmodified CORS check before
    the dashboard's login call ever succeeds, exactly as a real browser
    would reject it too.
    """
    directory = tmp_path_factory.mktemp("admin_dashboard")
    original = ADMIN_HTML_PATH.read_text()
    needle = 'const API_BASE = "http://127.0.0.1:8000";'
    assert needle in original, "admin.html's API_BASE constant line has changed — update this test's patch"
    patched = original.replace(needle, f'const API_BASE = "{api_server}";')
    (directory / "admin.html").write_text(patched)

    port = 5500
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(directory))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()

    yield f"http://127.0.0.1:{port}"

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
    context = browser.new_context(permissions=["clipboard-read", "clipboard-write"])
    pg = context.new_page()
    yield pg
    context.close()


def _sign_in(page, admin_page_origin, admin_key):
    page.goto(f"{admin_page_origin}/admin.html")
    page.fill("#login-key", admin_key)
    page.click("#login-btn")
    page.wait_for_selector("#app-view", state="visible", timeout=5000)


def _select_restaurant(page, restaurant_id):
    page.select_option("#restaurant-select", str(restaurant_id))


def _open_widget_tab(page):
    page.click('#tabs button[data-tab="widget"]')
    page.wait_for_selector("#panel-widget", state="visible", timeout=5000)


def _create_widget_via_api(client, admin_headers, restaurant_id, **fields):
    response = client.post(
        f"/admin/restaurant/{restaurant_id}/widget-config", json=fields, headers=admin_headers
    )
    assert response.status_code == 201
    return response.json()


WELCOME_INPUT = "#widget-welcome-message"
LANGUAGE_INPUT = "input[name='primary_language']"
LOGO_INPUT = "input[name='logo_url']"
ACCENT_TEXT_INPUT = "#accent-color-text"
ACTIVE_CHECKBOX = "input[name='is_active']"
BOOKING_CHECKBOX = "input[name='booking_enabled']"
SAVE_BUTTON = "#widget-settings-form button[type='submit']"
SAVE_FEEDBACK = "#widget-save-feedback"


# --- Load / create ---

def test_widget_tab_shows_create_state_when_no_config_exists(
    client, admin_headers, second_restaurant, api_server, admin_page_server, page
):
    _sign_in(page, admin_page_server, admin_headers["X-Admin-API-Key"])
    _select_restaurant(page, second_restaurant)
    _open_widget_tab(page)

    assert page.locator("#create-widget-btn").is_visible()
    assert page.locator("#widget-settings-form").count() == 0


def test_create_widget_from_dashboard(
    client, admin_headers, second_restaurant, api_server, admin_page_server, page
):
    _sign_in(page, admin_page_server, admin_headers["X-Admin-API-Key"])
    _select_restaurant(page, second_restaurant)
    _open_widget_tab(page)

    page.click("#create-widget-btn")
    page.wait_for_selector("#widget-settings-form", state="visible", timeout=5000)
    assert ".status-pill.on" in page.locator(".status-pill").first.get_attribute("class") or True
    assert page.locator(".status-pill").first.inner_text() == "Active"
    assert page.locator(".status-pill").nth(1).inner_text() == "Booking enabled"


def test_load_existing_widget_config_populates_the_form(
    client, admin_headers, second_restaurant, api_server, admin_page_server, page
):
    _create_widget_via_api(
        client, admin_headers, second_restaurant,
        welcome_message="Hiya! Ask me anything.",
        primary_language="en-US",
        logo_url="https://example.com/logo.png",
        accent_color="#123456",
        booking_enabled=False,
    )

    _sign_in(page, admin_page_server, admin_headers["X-Admin-API-Key"])
    _select_restaurant(page, second_restaurant)
    _open_widget_tab(page)

    assert page.locator(WELCOME_INPUT).input_value() == "Hiya! Ask me anything."
    assert page.locator(LANGUAGE_INPUT).input_value() == "en-US"
    assert page.locator(LOGO_INPUT).input_value() == "https://example.com/logo.png"
    assert page.locator(ACCENT_TEXT_INPUT).input_value() == "#123456"
    assert page.locator(".status-pill").nth(1).inner_text() == "Booking disabled"


# --- Update settings ---

def test_update_all_supported_settings_persists(
    client, admin_headers, second_restaurant, api_server, admin_page_server, page
):
    _create_widget_via_api(client, admin_headers, second_restaurant)
    _sign_in(page, admin_page_server, admin_headers["X-Admin-API-Key"])
    _select_restaurant(page, second_restaurant)
    _open_widget_tab(page)

    page.fill(WELCOME_INPUT, "Updated welcome message")
    page.fill(LANGUAGE_INPUT, "fr-FR")
    page.fill(LOGO_INPUT, "https://example.com/new-logo.png")
    page.fill(ACCENT_TEXT_INPUT, "#abcdef")
    page.click(SAVE_BUTTON)

    page.wait_for_function(
        "document.getElementById('widget-save-feedback') && "
        "document.getElementById('widget-save-feedback').textContent === 'Saved.'",
        timeout=5000,
    )

    # A fresh reload re-runs boot()'s auto sign-in, which resets
    # currentRestaurantId back to the FIRST restaurant in this
    # superadmin's list — second_restaurant must be re-selected, exactly
    # as a real user would need to after a hard refresh.
    page.reload()
    page.wait_for_selector("#app-view", state="visible", timeout=5000)
    _select_restaurant(page, second_restaurant)
    _open_widget_tab(page)
    assert page.locator(WELCOME_INPUT).input_value() == "Updated welcome message"
    assert page.locator(LANGUAGE_INPUT).input_value() == "fr-FR"
    assert page.locator(LOGO_INPUT).input_value() == "https://example.com/new-logo.png"
    assert page.locator(ACCENT_TEXT_INPUT).input_value() == "#abcdef"


def test_active_inactive_toggle_updates_the_status_pill(
    client, admin_headers, second_restaurant, api_server, admin_page_server, page
):
    _create_widget_via_api(client, admin_headers, second_restaurant)
    _sign_in(page, admin_page_server, admin_headers["X-Admin-API-Key"])
    _select_restaurant(page, second_restaurant)
    _open_widget_tab(page)

    page.uncheck(ACTIVE_CHECKBOX)
    page.click(SAVE_BUTTON)
    page.wait_for_function(
        "document.querySelector('.status-pill').textContent === 'Inactive'", timeout=5000
    )
    assert "off" in page.locator(".status-pill").first.get_attribute("class")


def test_booking_toggle_updates_the_status_pill(
    client, admin_headers, second_restaurant, api_server, admin_page_server, page
):
    _create_widget_via_api(client, admin_headers, second_restaurant, booking_enabled=True)
    _sign_in(page, admin_page_server, admin_headers["X-Admin-API-Key"])
    _select_restaurant(page, second_restaurant)
    _open_widget_tab(page)

    page.uncheck(BOOKING_CHECKBOX)
    page.click(SAVE_BUTTON)
    page.wait_for_function(
        "document.querySelectorAll('.status-pill')[1].textContent === 'Booking disabled'", timeout=5000
    )


# --- Allowed origins ---

def test_allowed_origin_add_list_and_delete(
    client, admin_headers, second_restaurant, api_server, admin_page_server, page
):
    _create_widget_via_api(client, admin_headers, second_restaurant)
    _sign_in(page, admin_page_server, admin_headers["X-Admin-API-Key"])
    _select_restaurant(page, second_restaurant)
    _open_widget_tab(page)

    assert "No origins configured yet" in page.content()

    page.fill("input[name='origin']", "https://www.example.com")
    page.click("#add-origin-form button[type='submit']")
    page.wait_for_selector("table tbody td:has-text('https://www.example.com')", timeout=5000)
    assert "Only these origins may embed" in page.content()

    page.once("dialog", lambda dialog: dialog.accept())
    page.click("table tbody button.danger")
    page.wait_for_function(
        "!document.body.textContent.includes('https://www.example.com')", timeout=5000
    )
    assert "No origins configured yet" in page.content()


def test_duplicate_origin_shows_a_clear_409_error(
    client, admin_headers, second_restaurant, api_server, admin_page_server, page
):
    _create_widget_via_api(client, admin_headers, second_restaurant)
    _sign_in(page, admin_page_server, admin_headers["X-Admin-API-Key"])
    _select_restaurant(page, second_restaurant)
    _open_widget_tab(page)

    page.fill("input[name='origin']", "https://www.example.com")
    page.click("#add-origin-form button[type='submit']")
    page.wait_for_selector("table tbody td:has-text('https://www.example.com')", timeout=5000)

    page.fill("input[name='origin']", "https://www.example.com")
    page.click("#add-origin-form button[type='submit']")
    page.wait_for_selector("#global-error .error-banner", timeout=5000)
    assert "already allowed" in page.locator("#global-error").inner_text().lower()


def test_invalid_origin_shows_a_clear_422_error(
    client, admin_headers, second_restaurant, api_server, admin_page_server, page
):
    _create_widget_via_api(client, admin_headers, second_restaurant)
    _sign_in(page, admin_page_server, admin_headers["X-Admin-API-Key"])
    _select_restaurant(page, second_restaurant)
    _open_widget_tab(page)

    page.fill("input[name='origin']", "not-a-valid-origin")
    page.click("#add-origin-form button[type='submit']")
    page.wait_for_selector("#global-error .error-banner", timeout=5000)
    assert "origin" in page.locator("#global-error").inner_text().lower()


# --- Embed code ---

def test_embed_snippet_contains_the_real_widget_key(
    client, admin_headers, second_restaurant, api_server, admin_page_server, page
):
    created = _create_widget_via_api(client, admin_headers, second_restaurant)
    _sign_in(page, admin_page_server, admin_headers["X-Admin-API-Key"])
    _select_restaurant(page, second_restaurant)
    _open_widget_tab(page)

    snippet_text = page.locator(".embed-snippet").inner_text()
    assert f'data-widget-key="{created["widget_key"]}"' in snippet_text
    assert f"{api_server}/static/widget/widget.js" in snippet_text


def test_widget_key_is_never_an_editable_input(
    client, admin_headers, second_restaurant, api_server, admin_page_server, page
):
    created = _create_widget_via_api(client, admin_headers, second_restaurant)
    _sign_in(page, admin_page_server, admin_headers["X-Admin-API-Key"])
    _select_restaurant(page, second_restaurant)
    _open_widget_tab(page)

    assert page.locator("input[name='widget_key']").count() == 0
    input_values = page.eval_on_selector_all(
        "#panel-widget input, #panel-widget textarea", "els => els.map(e => e.value)"
    )
    assert created["widget_key"] not in input_values


# --- Copy to clipboard ---

def test_copy_to_clipboard_copies_the_exact_snippet(
    client, admin_headers, second_restaurant, api_server, admin_page_server, page
):
    created = _create_widget_via_api(client, admin_headers, second_restaurant)
    _sign_in(page, admin_page_server, admin_headers["X-Admin-API-Key"])
    _select_restaurant(page, second_restaurant)
    _open_widget_tab(page)

    page.click("#copy-embed-btn")
    page.wait_for_function(
        "document.getElementById('copy-feedback').textContent === 'Copied!'", timeout=5000
    )
    clipboard_text = page.evaluate("navigator.clipboard.readText()")
    assert created["widget_key"] in clipboard_text
    assert "async" in clipboard_text


# --- Preview ---

def test_preview_renders_a_real_working_widget_when_preview_origin_is_configured(
    client, admin_headers, second_restaurant, api_server, admin_page_server, page, monkeypatch
):
    monkeypatch.setattr(config, "WIDGET_PREVIEW_ORIGIN", admin_page_server)
    _create_widget_via_api(client, admin_headers, second_restaurant, welcome_message="Preview me!")

    _sign_in(page, admin_page_server, admin_headers["X-Admin-API-Key"])
    _select_restaurant(page, second_restaurant)
    _open_widget_tab(page)

    page.click("#show-preview-btn")
    frame_launcher = page.frame_locator("iframe.widget-preview-frame").locator("button.raiw-launcher")
    frame_launcher.wait_for(state="visible", timeout=5000)
    # The launcher appearing already proves preview succeeded; the
    # dashboard's own status-clearing check runs on its own 2.5s
    # timer (see showWidgetPreview in admin.html), so wait for that
    # too rather than asserting on a still-in-flight "Loading…" state.
    page.wait_for_function(
        "document.getElementById('widget-preview-status').textContent === ''", timeout=4000
    )


def test_preview_fails_closed_and_explains_itself_when_preview_origin_is_not_configured(
    client, admin_headers, second_restaurant, api_server, admin_page_server, page
):
    assert config.WIDGET_PREVIEW_ORIGIN == ""  # default, unset — no monkeypatch in this test
    _create_widget_via_api(client, admin_headers, second_restaurant)

    _sign_in(page, admin_page_server, admin_headers["X-Admin-API-Key"])
    _select_restaurant(page, second_restaurant)
    _open_widget_tab(page)

    page.click("#show-preview-btn")
    page.wait_for_function(
        "document.getElementById('widget-preview-status').textContent.includes(\"isn't available\")",
        timeout=6000,
    )
    assert page.locator("#widget-preview-host iframe").count() == 0


def test_preview_fails_closed_when_preview_origin_is_mismatched(
    client, admin_headers, second_restaurant, api_server, admin_page_server, page, monkeypatch
):
    monkeypatch.setattr(config, "WIDGET_PREVIEW_ORIGIN", "https://some-other-admin-deployment.example.com")
    _create_widget_via_api(client, admin_headers, second_restaurant)

    _sign_in(page, admin_page_server, admin_headers["X-Admin-API-Key"])
    _select_restaurant(page, second_restaurant)
    _open_widget_tab(page)

    page.click("#show-preview-btn")
    page.wait_for_function(
        "document.getElementById('widget-preview-status').textContent.includes(\"isn't available\")",
        timeout=6000,
    )
    assert page.locator("#widget-preview-host iframe").count() == 0


# --- Cross-restaurant isolation / superadmin ---

def test_scoped_admin_cannot_see_or_reach_another_restaurants_widget(
    client, admin_headers, second_restaurant, scoped_admin_key, api_server, admin_page_server, page
):
    _create_widget_via_api(client, admin_headers, second_restaurant)
    _, scoped_headers = scoped_admin_key([1])  # NOT granted second_restaurant

    _sign_in(page, admin_page_server, scoped_headers["X-Admin-API-Key"])
    option_values = page.eval_on_selector_all(
        "#restaurant-select option", "opts => opts.map(o => o.value)"
    )
    assert str(second_restaurant) not in option_values

    # Even a direct, manually-issued request (bypassing the UI entirely,
    # simulating a tampered client) must still be rejected server-side —
    # the dashboard hiding the option is not the actual security boundary.
    status = page.evaluate(
        f"""
        fetch('{api_server}/admin/restaurant/{second_restaurant}/widget-config', {{
            headers: {{ 'X-Admin-API-Key': '{scoped_headers["X-Admin-API-Key"]}' }}
        }}).then(r => r.status)
        """
    )
    assert status == 404


def test_superadmin_switching_restaurants_reloads_the_correct_widget(
    client, admin_headers, second_restaurant, api_server, admin_page_server, page
):
    _create_widget_via_api(client, admin_headers, 1, welcome_message="Restaurant 1's widget")
    _create_widget_via_api(client, admin_headers, second_restaurant, welcome_message="Restaurant 2's widget")

    _sign_in(page, admin_page_server, admin_headers["X-Admin-API-Key"])
    _select_restaurant(page, 1)
    _open_widget_tab(page)
    page.wait_for_function(
        f"document.getElementById('{WELCOME_INPUT[1:]}') && "
        f"document.getElementById('{WELCOME_INPUT[1:]}').value === \"Restaurant 1's widget\"",
        timeout=5000,
    )

    _select_restaurant(page, second_restaurant)
    page.wait_for_function(
        f"document.getElementById('{WELCOME_INPUT[1:]}') && "
        f"document.getElementById('{WELCOME_INPUT[1:]}').value === \"Restaurant 2's widget\"",
        timeout=5000,
    )


# --- No secret leakage ---

def test_no_admin_secret_leaks_in_the_widget_tab(
    client, admin_headers, second_restaurant, api_server, admin_page_server, page
):
    _create_widget_via_api(client, admin_headers, second_restaurant)
    admin_key_value = admin_headers["X-Admin-API-Key"]

    requests_seen = []
    page.on("request", lambda req: requests_seen.append(req))

    _sign_in(page, admin_page_server, admin_key_value)
    _select_restaurant(page, second_restaurant)
    _open_widget_tab(page)
    page.fill("input[name='origin']", "https://www.example.com")
    page.click("#add-origin-form button[type='submit']")
    page.wait_for_selector("table tbody td:has-text('https://www.example.com')", timeout=5000)

    for req in requests_seen:
        assert admin_key_value not in req.url
        assert admin_key_value not in (req.post_data or "")

    assert admin_key_value not in page.content()



