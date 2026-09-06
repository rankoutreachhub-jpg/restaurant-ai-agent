"""
CORS must allow only the configured ALLOWED_ORIGINS (never "*"), so a
browser refuses to expose responses to a page running on any other
origin, while common localhost dev origins keep working out of the box.

These tests check the response headers Starlette's CORSMiddleware adds
(or omits) — actual cross-origin blocking is enforced by the browser
using those headers, not by the server refusing the request outright.
"""

from app import config

DISALLOWED_ORIGIN = "https://evil-example.com"


def test_allowed_origin_permitted_on_preflight(client):
    allowed = config.ALLOWED_ORIGINS[0]
    response = client.options(
        "/chat",
        headers={
            "Origin": allowed,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    assert response.status_code == 200
    assert response.headers.get("access-control-allow-origin") == allowed


def test_disallowed_origin_rejected_on_preflight(client):
    response = client.options(
        "/chat",
        headers={
            "Origin": DISALLOWED_ORIGIN,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    # Starlette's CORSMiddleware answers preflight requests itself, before
    # the route ever runs, and returns 400 when the origin isn't allowed.
    assert response.status_code == 400
    assert "access-control-allow-origin" not in response.headers


def test_allowed_origin_gets_cors_header_on_real_admin_request(client, admin_headers):
    allowed = config.ALLOWED_ORIGINS[0]
    response = client.get(
        "/admin/restaurant/1",
        headers={**admin_headers, "Origin": allowed},
    )
    assert response.status_code == 200
    assert response.headers.get("access-control-allow-origin") == allowed


def test_disallowed_origin_gets_no_cors_header_on_real_request(client):
    # The request itself still succeeds server-side — CORS is enforced by
    # the browser reading the response headers, not by the server
    # refusing to answer — but without this header a real browser would
    # refuse to let the page's JS read the response.
    response = client.get("/health", headers={"Origin": DISALLOWED_ORIGIN})
    assert response.status_code == 200
    assert "access-control-allow-origin" not in response.headers


def test_wildcard_is_not_used():
    """Regression guard: CORS must never fall back to allow_origins=["*"]."""
    assert "*" not in config.ALLOWED_ORIGINS


def test_conversation_token_header_is_exposed_on_allowed_origin(client, monkeypatch):
    """
    Stage 3 Step 5: a cross-origin frontend on an allowed origin must be
    able to read X-Conversation-Token via response.headers.get(...) in
    the browser — which requires Access-Control-Expose-Headers, not just
    the header being present on the wire. This must never widen the
    origin allowlist itself.
    """
    from types import SimpleNamespace
    from app import llm

    def fake_generate_content(model, contents, config):
        return SimpleNamespace(
            text="Hiya!",
            function_calls=[],
            candidates=[SimpleNamespace(content=SimpleNamespace(role="model", parts=[]))],
        )

    monkeypatch.setattr(llm.client.models, "generate_content", fake_generate_content)

    allowed = config.ALLOWED_ORIGINS[0]
    response = client.post(
        "/chat",
        json={"message": "hi", "history": [], "restaurant_id": 1},
        headers={"Origin": allowed},
    )
    assert response.status_code == 200
    assert "X-Conversation-Token" in response.headers
    exposed = response.headers.get("access-control-expose-headers", "")
    assert "X-Conversation-Token" in exposed
