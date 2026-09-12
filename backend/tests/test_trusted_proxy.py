"""
Trusted-proxy / real-client-IP handling for Railway (Security &
Production Hardening Audit finding C1).

app/rate_limit.py and app/auth.py both already correctly read
request.client.host as the one source of truth for "who is calling" --
no change was needed in either file, and neither is touched by this
change. The actual fix lives entirely at the uvicorn/process-config
layer: uvicorn's ProxyHeadersMiddleware (already enabled by uvicorn's
own --proxy-headers default -- see the Dockerfile CMD, which doesn't
pass --no-proxy-headers) rewrites request.client.host from
X-Forwarded-For, but ONLY for a connecting peer listed in
--forwarded-allow-ips / the FORWARDED_ALLOW_IPS environment variable
(uvicorn's own default: "127.0.0.1" -- i.e. trust nobody behind a proxy
that isn't on the same host, which Railway's edge is not).

These tests exercise that middleware wrapped around the real app, the
same way uvicorn wraps it in production, to prove:
  1. once trust is configured for the connecting peer (mirroring
     FORWARDED_ALLOW_IPS being set to a proxy's real, confirmed IP),
     rate limiting genuinely keys on the forwarded (real client)
     address, not the proxy's own -- and different forwarded clients
     get independent budgets.
  2. with NOTHING configured (today's default, and every other test
     file's setup) an X-Forwarded-For value has no effect at all --
     the anti-spoofing property the audit asked to preserve.
  3. app/config.py's validate_config() warns (without failing startup,
     and without changing any of the app's own request-time behavior)
     on a missing or unsafely-permissive ("*") FORWARDED_ALLOW_IPS.

TestClient's own default connecting-peer address is ("testclient", 50000)
(see starlette.testclient.TestClient's `client` parameter default) --
these tests use that string as the "trusted proxy" address to configure
the middleware against, exactly mirroring how a real deployment lists
its own proxy's real IP in FORWARDED_ALLOW_IPS.
"""

from starlette.testclient import TestClient
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from app import config
from app.main import app as fastapi_app
from app.rate_limit import admin_rate_limiter

ADMIN_ME_URL = "/admin/me"


def _wrapped_app():
    """The real app wrapped in ProxyHeadersMiddleware trusting ONLY
    TestClient's own default connecting-peer address -- mirroring
    FORWARDED_ALLOW_IPS being set to a real proxy's actual, confirmed IP
    in production. Never "*" -- a specific, known peer, exactly what the
    audit asked for."""
    return ProxyHeadersMiddleware(fastapi_app, trusted_hosts="testclient")


def test_forwarded_for_from_a_trusted_peer_is_honoured_and_rate_limits_key_on_it(admin_headers):
    with TestClient(_wrapped_app(), headers=admin_headers) as client:
        for _ in range(admin_rate_limiter.max_requests):
            response = client.get(ADMIN_ME_URL, headers={"X-Forwarded-For": "203.0.113.10"})
            assert response.status_code == 200

        blocked = client.get(ADMIN_ME_URL, headers={"X-Forwarded-For": "203.0.113.10"})
        assert blocked.status_code == 429

        # A DIFFERENT forwarded (real client) address has its own,
        # completely independent budget -- proving the limiter is
        # genuinely keying on the forwarded address rather than lumping
        # every request from behind the trusted proxy into one bucket.
        other_client = client.get(ADMIN_ME_URL, headers={"X-Forwarded-For": "203.0.113.20"})
        assert other_client.status_code == 200


def test_forwarded_for_from_an_untrusted_peer_has_no_effect(admin_headers):
    """Today's default posture (nothing configured, matching
    FORWARDED_ALLOW_IPS being unset) -- the raw app, with no proxy trust
    configured at all, exactly like every other test in this suite
    already runs it. An attacker-supplied X-Forwarded-For must never be
    honoured when no trust has been established."""
    with TestClient(fastapi_app, headers=admin_headers) as client:
        for _ in range(admin_rate_limiter.max_requests):
            response = client.get(ADMIN_ME_URL, headers={"X-Forwarded-For": "1.2.3.4"})
            assert response.status_code == 200

        # Still governed by the single shared "testclient" bucket -- a
        # spoofed X-Forwarded-For claiming a fresh IP changes nothing.
        blocked = client.get(ADMIN_ME_URL, headers={"X-Forwarded-For": "9.9.9.9"})
        assert blocked.status_code == 429


# --- app/config.py's startup guidance (informational only -- never
# raises, never changes request-time behavior) ---

def test_validate_config_warns_on_wildcard_forwarded_allow_ips(monkeypatch, capsys):
    monkeypatch.setattr(config, "FORWARDED_ALLOW_IPS", "*")
    config.validate_config()
    assert "FORWARDED_ALLOW_IPS is set to '*'" in capsys.readouterr().out


def test_validate_config_notes_when_forwarded_allow_ips_is_unset(monkeypatch, capsys):
    monkeypatch.setattr(config, "FORWARDED_ALLOW_IPS", "")
    config.validate_config()
    assert "FORWARDED_ALLOW_IPS is not set" in capsys.readouterr().out


def test_validate_config_is_quiet_when_forwarded_allow_ips_is_a_real_value(monkeypatch, capsys):
    monkeypatch.setattr(config, "FORWARDED_ALLOW_IPS", "10.0.0.1")
    config.validate_config()
    assert "FORWARDED_ALLOW_IPS" not in capsys.readouterr().out
