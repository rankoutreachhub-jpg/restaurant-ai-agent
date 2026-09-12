"""
Real-browser tests for the admin-user key delivery flow in the Platform
Admin tab of frontend/admin.html (safer/cleaner admin-access delivery
phase). Mirrors tests/test_admin_widget_dashboard.py's pattern exactly:
a real server (the same `app` object the rest of this suite uses) and a
real browser driving admin.html itself.

No backend behavior changed for this phase -- the plaintext key was
already never persisted and never returned by any GET (see
tests/test_platform_admin.py); this file exercises the new one-time
reveal UI (copy button, status message, onboarding instructions) and
confirms the key never leaks into the admin-users table afterward.
"""

import functools
import http.server
import re
import socket
import threading
import time
from pathlib import Path

import pytest

playwright_sync_api = pytest.importorskip("playwright.sync_api")
sync_playwright = playwright_sync_api.sync_playwright

import uvicorn

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
    """See tests/test_admin_widget_dashboard.py's namesake fixture for
    the full rationale (port 5500 specifically, byte-identical patched
    copy, never touching frontend/admin.html itself)."""
    directory = tmp_path_factory.mktemp("platform_admin_dashboard")
    original = ADMIN_HTML_PATH.read_text()
    api_base_pattern = re.compile(r'const API_BASE = "[^"]*";')
    assert api_base_pattern.search(original), "admin.html's API_BASE constant line has changed — update this test's patch"
    patched = api_base_pattern.sub(f'const API_BASE = "{api_server}";', original, count=1)
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


def _open_platform_tab(page):
    page.click("#platform-tab-btn")
    page.wait_for_selector("#panel-platform", state="visible", timeout=5000)


def _create_admin_user_via_ui(page, label, restaurant_id):
    page.fill("#create-admin-user-form input[name='label']", label)
    page.select_option("#create-admin-user-form select[name='restaurant_ids']", str(restaurant_id))
    page.click("#create-admin-user-form button[type='submit']")
    page.wait_for_selector("#key-reveal-slot .key-reveal", state="visible", timeout=5000)


def test_new_admin_user_reveal_shows_one_time_warning_and_copy_button(
    client, admin_headers, second_restaurant, api_server, admin_page_server, page
):
    _sign_in(page, admin_page_server, admin_headers["X-Admin-API-Key"])
    _open_platform_tab(page)
    _create_admin_user_via_ui(page, "Front of house", second_restaurant)

    reveal = page.locator("#key-reveal-slot .key-reveal")
    assert "Copy this key now" in reveal.inner_text()
    assert "it will not be shown again" in reveal.inner_text()
    assert page.locator("#copy-key-reveal-btn").is_visible()
    assert page.locator("#key-reveal-code").inner_text().startswith("ra_")


def test_reveal_includes_onboarding_instructions_without_recommending_unsafe_sharing(
    client, admin_headers, second_restaurant, api_server, admin_page_server, page
):
    _sign_in(page, admin_page_server, admin_headers["X-Admin-API-Key"])
    _open_platform_tab(page)
    _create_admin_user_via_ui(page, "Onboarding instructions test", second_restaurant)

    reveal_text = page.locator("#key-reveal-slot .key-reveal").inner_text()
    assert "restaurant owner" in reveal_text
    assert "Jantar AI admin dashboard" in reveal_text
    assert "sign in" in reveal_text
    # Explicitly steers away from unsafe public sharing rather than being silent about it.
    assert "public" in reveal_text.lower()


def test_copy_button_copies_the_exact_key_and_shows_status(
    client, admin_headers, second_restaurant, api_server, admin_page_server, page
):
    _sign_in(page, admin_page_server, admin_headers["X-Admin-API-Key"])
    _open_platform_tab(page)
    _create_admin_user_via_ui(page, "Copy button test", second_restaurant)

    revealed_key = page.locator("#key-reveal-code").inner_text()
    page.click("#copy-key-reveal-btn")
    page.wait_for_function(
        "document.getElementById('key-reveal-copy-status').textContent === 'Copied to clipboard.'",
        timeout=3000,
    )

    clipboard_text = page.evaluate("navigator.clipboard.readText()")
    assert clipboard_text == revealed_key


def test_admin_users_table_never_shows_the_plaintext_key_after_reveal(
    client, admin_headers, second_restaurant, api_server, admin_page_server, page
):
    _sign_in(page, admin_page_server, admin_headers["X-Admin-API-Key"])
    _open_platform_tab(page)
    _create_admin_user_via_ui(page, "Should not leak", second_restaurant)
    revealed_key = page.locator("#key-reveal-code").inner_text()

    # A fresh reload re-runs boot()'s sign-in and reloads the tab from
    # scratch -- the key-reveal slot is gone, and the admin-users table
    # (rendered straight from GET /admin/platform/admin-users) must never
    # contain the plaintext key anywhere on the page.
    page.reload()
    page.wait_for_selector("#app-view", state="visible", timeout=5000)
    _open_platform_tab(page)

    assert page.locator("#key-reveal-slot .key-reveal").count() == 0
    full_page_text = page.locator("body").inner_text()
    assert revealed_key not in full_page_text


def test_admin_users_table_has_clear_columns_and_no_plaintext_key(
    client, admin_headers, second_restaurant, api_server, admin_page_server, page
):
    _sign_in(page, admin_page_server, admin_headers["X-Admin-API-Key"])
    _open_platform_tab(page)
    _create_admin_user_via_ui(page, "Table columns test", second_restaurant)
    page.reload()
    page.wait_for_selector("#app-view", state="visible", timeout=5000)
    _open_platform_tab(page)

    headers = page.locator("h2:has-text('Admin users') + table thead th").all_inner_texts()
    assert headers == ["#", "Label", "Status", "Restaurant(s)", "Actions", "Access"]

    # This module's other tests each create their own admin user against
    # the same long-lived module-scoped backend, so the table holds more
    # than just this test's row by now -- find this one specifically
    # rather than assuming it's first.
    row_text = page.locator("h2:has-text('Admin users') + table tbody tr", has_text="Table columns test").inner_text()
    assert "Active" in row_text
    assert str(second_restaurant) in row_text


def test_rotation_still_works_from_the_dashboard(
    client, admin_headers, second_restaurant, api_server, admin_page_server, page
):
    _sign_in(page, admin_page_server, admin_headers["X-Admin-API-Key"])
    _open_platform_tab(page)
    _create_admin_user_via_ui(page, "Rotation still works", second_restaurant)
    old_key = page.locator("#key-reveal-code").inner_text()

    page.once("dialog", lambda dialog: dialog.accept())
    page.locator("tr", has_text="Rotation still works").locator("button", has_text="Rotate key").click()
    # A plain wait_for_selector for ".key-reveal" would resolve immediately
    # since the CREATE step's reveal is already visible -- wait for the
    # reveal's own content to actually change instead.
    page.wait_for_function(
        "(oldKey) => { const el = document.getElementById('key-reveal-code'); return el && el.textContent !== oldKey; }",
        arg=old_key,
        timeout=5000,
    )
    new_key = page.locator("#key-reveal-code").inner_text()

    assert new_key != old_key
    assert new_key.startswith("ra_")
    old_key_response = client.get(
        f"/admin/restaurant/{second_restaurant}", headers={"X-Admin-API-Key": old_key}
    )
    assert old_key_response.status_code == 401
    new_key_response = client.get(
        f"/admin/restaurant/{second_restaurant}", headers={"X-Admin-API-Key": new_key}
    )
    assert new_key_response.status_code == 200
