"""
GET /static/widget/widget.js (Stage 4 Phase E): the embeddable widget
script itself, served as a plain static asset. Deliberately mounted
outside /widget/* — these tests exist specifically to prove that
choice holds: the Phase D CORS middleware (app/widget_cors.py), which
only inspects paths starting with "/widget/", must never see or
interfere with this path.
"""

from pathlib import Path

WIDGET_JS_PATH = (
    Path(__file__).resolve().parent.parent / "app" / "static_widget" / "widget.js"
)


def test_widget_js_is_served_successfully(client):
    response = client.get("/static/widget/widget.js")
    assert response.status_code == 200


def test_widget_js_has_a_javascript_content_type(client):
    response = client.get("/static/widget/widget.js")
    content_type = response.headers.get("content-type", "")
    assert "javascript" in content_type


def test_widget_js_content_matches_the_file_on_disk(client):
    response = client.get("/static/widget/widget.js")
    assert response.content == WIDGET_JS_PATH.read_bytes()


def test_widget_js_is_not_affected_by_the_widget_cors_middleware(client):
    """
    Regression guard for the Phase E static-serving design decision:
    this path is NOT under /widget/*, so a disallowed-looking Origin
    header must have no effect here at all (unlike a real /widget/*
    request, which would 403 with no CORS headers for an origin that
    isn't registered — see tests/test_widget_cors.py).
    """
    response = client.get(
        "/static/widget/widget.js", headers={"Origin": "https://not-a-registered-origin.example.com"}
    )
    assert response.status_code == 200


def test_unknown_static_widget_path_returns_a_plain_404(client):
    response = client.get("/static/widget/does-not-exist.js")
    assert response.status_code == 404


# --- Final QA fix A4: widget footer gains a Terms of Service link ---

def test_widget_js_footer_links_to_terms_of_service_alongside_privacy_policy():
    content = WIDGET_JS_PATH.read_text()
    assert 'var PRIVACY_POLICY_URL = "https://restaurant-ai-agent-eight.vercel.app/privacy-policy.html";' in content
    assert 'var TERMS_OF_SERVICE_URL = "https://restaurant-ai-agent-eight.vercel.app/terms-of-service.html";' in content
    assert 'termsLinkEl.href = TERMS_OF_SERVICE_URL;' in content
    assert 'termsLinkEl.textContent = "Terms of Service";' in content
    # Same accessibility/style treatment as the pre-existing Privacy link.
    assert 'termsLinkEl.className = "raiw-footer-link";' in content
    assert 'termsLinkEl.tabIndex = 0;' in content
    # The existing Privacy link itself must be unchanged.
    assert 'privacyLinkEl.href = PRIVACY_POLICY_URL;' in content
    assert 'privacyLinkEl.textContent = "Privacy Policy";' in content
