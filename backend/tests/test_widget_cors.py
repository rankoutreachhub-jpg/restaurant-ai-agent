"""
Strict per-restaurant CORS enforcement for the public widget surface
(Stage 4 Phase D — app/widget_cors.py). Unlike tests/test_cors.py (which
covers the app-wide CORSMiddleware governing /admin, /chat, etc.), every
test here targets /widget/* paths, where a custom outer middleware makes
its own CORS decision per-widget using app/models.py:WidgetAllowedOrigin
rows — never the global ALLOWED_ORIGINS list.

Both public widget routes are exercised: GET .../config (no side effects,
safe to call freely) and POST .../chat (stubs the Gemini call exactly like
tests/test_widget_chat.py, since a real reply is required before the
route returns 200).
"""

from types import SimpleNamespace

import pytest

from app import config
from app import llm as llm_module

DISALLOWED_ORIGIN = "https://evil-example.com"


def _config_url(widget_key):
    return f"/widget/{widget_key}/config"


def _chat_url(widget_key):
    return f"/widget/{widget_key}/chat"


def _create_widget_config(client, admin_headers, restaurant_id, **fields):
    # The restaurant already has an auto-provisioned (is_active=False)
    # config the moment it's created (see
    # routers/platform_admin.py:create_restaurant) -- this call now
    # UPDATES that row rather than creating a fresh one, so tests in this
    # file (about CORS enforcement on a reachable widget) must explicitly
    # re-activate it unless deliberately testing the inactive case.
    fields.setdefault("is_active", True)
    response = client.post(
        f"/admin/restaurant/{restaurant_id}/widget-config", json=fields, headers=admin_headers
    )
    assert response.status_code == 201
    return response.json()


def _add_origin(client, admin_headers, restaurant_id, origin):
    response = client.post(
        f"/admin/restaurant/{restaurant_id}/widget-config/origins",
        json={"origin": origin},
        headers=admin_headers,
    )
    assert response.status_code == 201


def _fake_response(text="Hiya!"):
    return SimpleNamespace(
        text=text,
        function_calls=[],
        candidates=[SimpleNamespace(content=SimpleNamespace(role="model", parts=[]))],
    )


def _stub_plain_reply(monkeypatch, text="Hiya!"):
    def fake_generate_content(model, contents, config):
        return _fake_response(text=text)

    monkeypatch.setattr(llm_module.client.models, "generate_content", fake_generate_content)


# --- Zero configured origins: fail closed ---

def test_zero_configured_origins_fails_closed_on_config_endpoint(client, admin_headers):
    created = _create_widget_config(client, admin_headers, 1)
    response = client.get(_config_url(created["widget_key"]), headers={"Origin": "https://example.com"})
    assert response.status_code == 403
    assert "access-control-allow-origin" not in response.headers


def test_zero_configured_origins_fails_closed_on_chat_endpoint(client, admin_headers, monkeypatch):
    created = _create_widget_config(client, admin_headers, 1)
    _stub_plain_reply(monkeypatch)
    response = client.post(
        _chat_url(created["widget_key"]),
        json={"message": "hi"},
        headers={"Origin": "https://example.com"},
    )
    assert response.status_code == 403
    assert "access-control-allow-origin" not in response.headers


# --- Exact match success ---

def test_exact_matching_origin_succeeds_on_config_endpoint(client, admin_headers, second_restaurant):
    created = _create_widget_config(client, admin_headers, second_restaurant)
    _add_origin(client, admin_headers, second_restaurant, "https://example.com")

    response = client.get(_config_url(created["widget_key"]), headers={"Origin": "https://example.com"})
    assert response.status_code == 200
    assert response.headers.get("access-control-allow-origin") == "https://example.com"
    assert response.headers.get("vary") == "Origin"


def test_exact_matching_origin_succeeds_on_chat_endpoint(client, admin_headers, second_restaurant, monkeypatch):
    created = _create_widget_config(client, admin_headers, second_restaurant)
    _add_origin(client, admin_headers, second_restaurant, "https://example.com")
    _stub_plain_reply(monkeypatch)

    response = client.post(
        _chat_url(created["widget_key"]),
        json={"message": "hi"},
        headers={"Origin": "https://example.com"},
    )
    assert response.status_code == 200
    assert response.headers.get("access-control-allow-origin") == "https://example.com"


# --- Mismatched origin: fail closed, no CORS headers ---

def test_mismatched_origin_is_rejected_with_no_cors_headers(client, admin_headers, second_restaurant):
    created = _create_widget_config(client, admin_headers, second_restaurant)
    _add_origin(client, admin_headers, second_restaurant, "https://example.com")

    response = client.get(_config_url(created["widget_key"]), headers={"Origin": DISALLOWED_ORIGIN})
    assert response.status_code == 403
    assert "access-control-allow-origin" not in response.headers


# --- Multiple allowed origins ---

def test_multiple_allowed_origins_each_work_independently(client, admin_headers, second_restaurant):
    created = _create_widget_config(client, admin_headers, second_restaurant)
    _add_origin(client, admin_headers, second_restaurant, "https://example.com")
    _add_origin(client, admin_headers, second_restaurant, "https://www.example.com")

    for origin in ("https://example.com", "https://www.example.com"):
        response = client.get(_config_url(created["widget_key"]), headers={"Origin": origin})
        assert response.status_code == 200
        assert response.headers.get("access-control-allow-origin") == origin

    response = client.get(_config_url(created["widget_key"]), headers={"Origin": "https://other.example.com"})
    assert response.status_code == 403


# --- No Origin header: not a cross-origin browser request ---

def test_no_origin_header_passes_through_untouched_even_with_zero_configured_origins(
    client, admin_headers
):
    created = _create_widget_config(client, admin_headers, 1)
    response = client.get(_config_url(created["widget_key"]))
    assert response.status_code == 200
    assert "access-control-allow-origin" not in response.headers


# --- OPTIONS / preflight ---

def test_preflight_succeeds_for_an_allowed_origin_on_config_endpoint(client, admin_headers, second_restaurant):
    created = _create_widget_config(client, admin_headers, second_restaurant)
    _add_origin(client, admin_headers, second_restaurant, "https://example.com")

    response = client.options(
        _config_url(created["widget_key"]),
        headers={
            "Origin": "https://example.com",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert response.status_code == 200
    assert response.headers.get("access-control-allow-origin") == "https://example.com"
    assert response.headers.get("access-control-allow-methods") == "GET, POST, OPTIONS"
    assert response.headers.get("access-control-max-age") == "600"


def test_preflight_succeeds_for_an_allowed_origin_on_chat_endpoint_and_allows_conversation_token_header(
    client, admin_headers, second_restaurant
):
    created = _create_widget_config(client, admin_headers, second_restaurant)
    _add_origin(client, admin_headers, second_restaurant, "https://example.com")

    response = client.options(
        _chat_url(created["widget_key"]),
        headers={
            "Origin": "https://example.com",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type, x-conversation-token",
        },
    )
    assert response.status_code == 200
    allow_headers = response.headers.get("access-control-allow-headers", "")
    assert "Content-Type" in allow_headers
    assert "X-Conversation-Token" in allow_headers


def test_preflight_is_rejected_for_a_disallowed_origin(client, admin_headers, second_restaurant):
    created = _create_widget_config(client, admin_headers, second_restaurant)
    _add_origin(client, admin_headers, second_restaurant, "https://example.com")

    response = client.options(
        _config_url(created["widget_key"]),
        headers={"Origin": DISALLOWED_ORIGIN, "Access-Control-Request-Method": "GET"},
    )
    assert response.status_code == 403
    assert "access-control-allow-origin" not in response.headers


def test_preflight_is_rejected_when_zero_origins_are_configured(client, admin_headers):
    created = _create_widget_config(client, admin_headers, 1)
    response = client.options(
        _config_url(created["widget_key"]),
        headers={"Origin": "https://example.com", "Access-Control-Request-Method": "GET"},
    )
    assert response.status_code == 403


# --- Unknown / inactive widget: identical behavior to non-preflight 404 ---

def test_unknown_widget_options_returns_the_same_generic_404_as_a_real_get_request(client):
    real_response = client.get(_config_url("wgt_never_issued"))
    preflight_response = client.options(
        _config_url("wgt_never_issued"),
        headers={"Origin": "https://example.com", "Access-Control-Request-Method": "GET"},
    )
    assert preflight_response.status_code == real_response.status_code == 404
    assert preflight_response.json() == real_response.json()


def test_inactive_widget_options_returns_generic_404(client, admin_headers, second_restaurant):
    created = _create_widget_config(client, admin_headers, second_restaurant, is_active=False)
    response = client.options(
        _config_url(created["widget_key"]),
        headers={"Origin": "https://example.com", "Access-Control-Request-Method": "GET"},
    )
    assert response.status_code == 404


def test_unknown_widget_real_request_with_origin_still_returns_existing_404_not_403(client):
    response = client.get(_config_url("wgt_never_issued"), headers={"Origin": "https://example.com"})
    assert response.status_code == 404


def test_inactive_widget_real_request_with_origin_still_returns_existing_404_not_403(
    client, admin_headers, second_restaurant
):
    created = _create_widget_config(client, admin_headers, second_restaurant, is_active=False)
    response = client.get(
        _config_url(created["widget_key"]), headers={"Origin": "https://example.com"}
    )
    assert response.status_code == 404


# --- X-Conversation-Token exposure on real chat responses ---

def test_conversation_token_header_is_exposed_on_allowed_origin_chat_response(
    client, admin_headers, second_restaurant, monkeypatch
):
    created = _create_widget_config(client, admin_headers, second_restaurant)
    _add_origin(client, admin_headers, second_restaurant, "https://example.com")
    _stub_plain_reply(monkeypatch)

    response = client.post(
        _chat_url(created["widget_key"]),
        json={"message": "hi"},
        headers={"Origin": "https://example.com"},
    )
    assert response.status_code == 200
    assert "X-Conversation-Token" in response.headers
    exposed = response.headers.get("access-control-expose-headers", "")
    assert "X-Conversation-Token" in exposed


# --- Credentials never enabled ---

def test_access_control_allow_credentials_is_never_set(client, admin_headers, second_restaurant):
    created = _create_widget_config(client, admin_headers, second_restaurant)
    _add_origin(client, admin_headers, second_restaurant, "https://example.com")

    response = client.get(_config_url(created["widget_key"]), headers={"Origin": "https://example.com"})
    assert "access-control-allow-credentials" not in response.headers


# --- Tenant isolation ---

def test_one_restaurants_allowed_origin_never_validates_against_another_restaurants_widget(
    client, admin_headers, second_restaurant
):
    config_1 = _create_widget_config(client, admin_headers, 1)
    config_2 = _create_widget_config(client, admin_headers, second_restaurant)
    _add_origin(client, admin_headers, 1, "https://restaurant-1-site.example.com")
    _add_origin(client, admin_headers, second_restaurant, "https://restaurant-2-site.example.com")

    # Restaurant 1's own allowed origin works for restaurant 1's widget...
    response = client.get(
        _config_url(config_1["widget_key"]),
        headers={"Origin": "https://restaurant-1-site.example.com"},
    )
    assert response.status_code == 200

    # ...but must NOT validate against restaurant 2's widget_key.
    response = client.get(
        _config_url(config_2["widget_key"]),
        headers={"Origin": "https://restaurant-1-site.example.com"},
    )
    assert response.status_code == 403


# --- Global CORS regression ---

def test_widget_preflight_from_an_origin_outside_the_global_allowlist_still_succeeds(
    client, admin_headers, second_restaurant
):
    """
    Proves the middleware ordering actually isolates /widget/* from the
    app-wide ALLOWED_ORIGINS list: an origin that is NOT in
    config.ALLOWED_ORIGINS (so the global CORSMiddleware would reject a
    preflight for e.g. /chat, per tests/test_cors.py) must still succeed
    here purely because it's registered as this widget's own allowed
    origin.
    """
    assert "https://a-real-restaurant-website.example.com" not in config.ALLOWED_ORIGINS

    created = _create_widget_config(client, admin_headers, second_restaurant)
    _add_origin(client, admin_headers, second_restaurant, "https://a-real-restaurant-website.example.com")

    response = client.options(
        _config_url(created["widget_key"]),
        headers={
            "Origin": "https://a-real-restaurant-website.example.com",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert response.status_code == 200
    assert (
        response.headers.get("access-control-allow-origin")
        == "https://a-real-restaurant-website.example.com"
    )


def test_chat_endpoint_preflight_is_unaffected_by_widget_cors_middleware(client):
    """
    /chat (legacy, non-widget) must still be governed entirely by the
    existing global CORSMiddleware, exactly as tests/test_cors.py already
    covers — this is a light regression check that the new middleware's
    path-prefix check correctly excludes it, not a duplicate of that
    file's own coverage.
    """
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


# --- WIDGET_PREVIEW_ORIGIN carve-out ---

def test_widget_preview_origin_is_permitted_against_any_widget_even_when_not_registered(
    client, admin_headers, second_restaurant, monkeypatch
):
    monkeypatch.setattr(config, "WIDGET_PREVIEW_ORIGIN", "https://preview.internal.example.com")

    created = _create_widget_config(client, admin_headers, second_restaurant)
    # Deliberately zero registered origins for this widget.
    response = client.get(
        _config_url(created["widget_key"]),
        headers={"Origin": "https://preview.internal.example.com"},
    )
    assert response.status_code == 200
    assert (
        response.headers.get("access-control-allow-origin")
        == "https://preview.internal.example.com"
    )


def test_widget_preview_origin_carve_out_is_inert_when_unset(client, admin_headers, second_restaurant):
    assert config.WIDGET_PREVIEW_ORIGIN == ""
    created = _create_widget_config(client, admin_headers, second_restaurant)
    response = client.get(
        _config_url(created["widget_key"]),
        headers={"Origin": "https://some-random-origin.example.com"},
    )
    assert response.status_code == 403


# --- CVE-2026-48710 ("BadHost") regression ---
#
# Pre-fix Starlette (<1.0.1) reconstructed request.url by concatenating
# the raw, unvalidated Host header with the request path. A Host value
# containing "/", "?", or "#" could desync request.url.path (what
# middleware sees) from the real routed path (what the ASGI server
# actually dispatched on) — letting a request that's really hitting
# /widget/{key}/... appear, to path-string-based middleware like
# widget_cors_middleware's _extract_widget_key(), as if it weren't a
# /widget/* request at all. That would mean this middleware's whole
# CORS decision (see app/widget_cors.py) gets silently skipped, falling
# through to the global CORSMiddleware's own (unrelated, wider)
# ALLOWED_ORIGINS instead — defeating the per-restaurant origin
# isolation this middleware exists to enforce.
#
# This asserts the concrete, observable symptom: a disallowed-origin
# request to a real /widget/*/config endpoint must still fail closed
# (403, no CORS header) even when the Host header is deliberately
# poisoned with a path-shifting character. Requires Starlette >=1.0.1
# (see requirements.txt) — this is a regression test, not something
# app/widget_cors.py's own code changed to satisfy.

_MALFORMED_HOSTS = [
    "testserver/../admin/restaurant/1",
    "testserver?x=/admin",
    "testserver#/admin",
    "evil.com",
]


@pytest.mark.parametrize("malformed_host", _MALFORMED_HOSTS)
def test_malformed_host_header_does_not_bypass_widget_cors(
    client, admin_headers, malformed_host
):
    created = _create_widget_config(client, admin_headers, 1)

    response = client.get(
        _config_url(created["widget_key"]),
        headers={"Origin": DISALLOWED_ORIGIN, "Host": malformed_host},
    )

    assert response.status_code == 403
    assert "access-control-allow-origin" not in response.headers
