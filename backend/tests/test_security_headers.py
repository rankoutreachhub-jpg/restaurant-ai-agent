"""
Baseline security response headers and API-docs gating (Security &
Production Hardening Audit finding D1).

app/security_headers.py adds a handful of fixed headers to every
response; app/config.py's DISABLE_DOCS (off by default, preserving this
project's own documented "try /docs" workflow) controls whether
FastAPI's /docs, /redoc, and /openapi.json exist at all — see both
modules' docstrings for the full rationale. This file checks both.
"""

from starlette.testclient import TestClient

from app.main import _docs_kwargs, app as fastapi_app


# --- Headers present on ordinary responses ---

def test_security_headers_present_on_a_normal_response(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "strict-origin-when-cross-origin"
    assert response.headers["x-frame-options"] == "DENY"


def test_security_headers_present_on_an_authenticated_admin_response(client, admin_headers):
    response = client.get("/admin/restaurant/1", headers=admin_headers)
    assert response.status_code == 200
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "strict-origin-when-cross-origin"
    assert response.headers["x-frame-options"] == "DENY"


def test_security_headers_present_on_an_unmatched_404_route(client):
    response = client.get("/this-route-does-not-exist")
    assert response.status_code == 404
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"


def test_security_headers_present_even_on_a_response_widget_cors_middleware_answers_directly(client):
    """widget_cors_middleware answers every OPTIONS request to /widget/*
    itself, without ever calling the wrapped app (see its own module
    docstring) -- security_headers_middleware is registered as the new
    OUTERMOST layer specifically so these headers still land here too."""
    response = client.options(
        "/widget/wgt_this_key_does_not_exist/config",
        headers={
            "Origin": "https://example.com",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert response.status_code == 404  # widget_cors_middleware's own _not_found_response
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "strict-origin-when-cross-origin"
    assert response.headers["x-frame-options"] == "DENY"


# --- Strict-Transport-Security: only ever over an actually-https request ---

def test_hsts_is_not_sent_over_plain_http(client):
    response = client.get("/health")
    assert "strict-transport-security" not in response.headers


def test_hsts_is_sent_when_the_request_scheme_is_https():
    https_client = TestClient(fastapi_app, base_url="https://testserver")
    response = https_client.get("/health")
    assert response.status_code == 200
    assert response.headers["strict-transport-security"] == "max-age=63072000; includeSubDomains"


# --- API docs: enabled by default, gate-able via config.DISABLE_DOCS ---

def test_docs_redoc_and_openapi_are_reachable_by_default(client):
    """Preserves this project's own documented local/Docker workflow
    (README explicitly says to try /docs) -- DISABLE_DOCS is unset in
    the test environment, matching that default."""
    assert client.get("/docs").status_code == 200
    assert client.get("/redoc").status_code == 200
    assert client.get("/openapi.json").status_code == 200


def test_docs_kwargs_disables_all_three_urls_when_requested():
    assert _docs_kwargs(disable_docs=True) == {
        "docs_url": None,
        "redoc_url": None,
        "openapi_url": None,
    }


def test_docs_kwargs_enables_the_standard_urls_by_default():
    assert _docs_kwargs(disable_docs=False) == {
        "docs_url": "/docs",
        "redoc_url": "/redoc",
        "openapi_url": "/openapi.json",
    }
