"""
CORS on the API.

Inert in production — the browser only ever talks to the Next service, which
proxies here server-to-server with no Origin header — but wrong CORS is the
first thing blamed for a "backend unavailable", so the settings are pinned
rather than left to be re-derived from the symptom next time.
"""

import pytest
from fastapi.testclient import TestClient

from server.main import app

LOCAL = "http://localhost:3000"
RENDER = "https://accounting-web.onrender.com"
HOSTILE = "https://evil.example.com"


@pytest.fixture()
def client():
    return TestClient(app)


def test_the_local_dev_origin_is_allowed(client):
    """`next dev` on :3000 against uvicorn on :8000 — the one case where CORS
    is actually on the path."""
    res = client.get("/api/health", headers={"Origin": LOCAL})
    assert res.status_code == 200
    assert res.headers["access-control-allow-origin"] == LOCAL


def test_a_render_origin_is_allowed_by_regex(client):
    """Render mints a hostname per service and adds a suffix on rename, so an
    explicit allowlist goes stale silently."""
    res = client.get("/api/health", headers={"Origin": RENDER})
    assert res.status_code == 200
    assert res.headers["access-control-allow-origin"] == RENDER


def test_an_unrelated_origin_is_not_granted_access(client):
    """allow_origins is not "*" — with credentials enabled the spec forbids the
    wildcard, and a browser would reject the response anyway."""
    res = client.get("/api/health", headers={"Origin": HOSTILE})
    assert res.headers.get("access-control-allow-origin") not in (HOSTILE, "*")


def test_preflight_permits_the_authorization_header(client):
    """Every data call carries a Bearer token; a preflight that refuses the
    header blocks the lot."""
    res = client.options("/api/dashboard", headers={
        "Origin": LOCAL,
        "Access-Control-Request-Method": "GET",
        "Access-Control-Request-Headers": "authorization,content-type",
    })
    assert res.status_code == 200
    assert res.headers["access-control-allow-origin"] == LOCAL
    allowed = res.headers.get("access-control-allow-headers", "").lower()
    assert "authorization" in allowed


def test_credentials_are_allowed(client):
    res = client.get("/api/health", headers={"Origin": LOCAL})
    assert res.headers.get("access-control-allow-credentials") == "true"


def test_content_disposition_is_exposed_for_the_csv_download(client):
    """The export's filename lives in Content-Disposition, and cross-origin JS
    cannot read a header that is not exposed — the file would save under a name
    the browser invented."""
    res = client.get("/api/health", headers={"Origin": LOCAL})
    exposed = res.headers.get("access-control-expose-headers", "")
    assert "Content-Disposition" in exposed


def test_the_webhook_is_reachable_without_an_origin(client):
    """Stripe posts server-to-server: no Origin header, so CORS must not be
    involved at all. A 503 here is the unconfigured-secret path, which is
    exactly what the suite's config produces — the point is that it is not a
    CORS rejection."""
    res = client.post("/api/v1/webhooks/stripe", json={})
    assert res.status_code in (400, 503)
