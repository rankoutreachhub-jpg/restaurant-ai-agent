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
