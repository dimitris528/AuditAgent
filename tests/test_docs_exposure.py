"""
The interactive API documentation is opt-in.

/docs, /redoc and /openapi.json publish a complete, accurate map of the API to
anyone who asks. Every route behind them is auth-gated, so this is not a breach
on its own — but it is free reconnaissance, and it is the only public surface
here that serves no customer. The launch decision was to ship it OFF and let a
developer turn it on locally.

The failure worth catching is the quiet one: someone adds a route, FastAPI
re-enables the schema, and nobody notices because nothing breaks. So these
tests assert the routes are ABSENT by default rather than merely unreachable.
"""

import os
import subprocess
import sys
import textwrap

from fastapi.testclient import TestClient

import config
from server.main import app

DOC_PATHS = ("/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect")


# --------------------------------------------------------------------------
# The default: off
# --------------------------------------------------------------------------
def test_the_suite_runs_with_docs_disabled():
    """Guards the premise of every test below — conftest sets no DOCS_ENABLED,
    so this is the default talking, not a fixture."""
    assert config.DOCS_ENABLED is False


def test_the_doc_routes_are_not_registered_at_all():
    """Not registered, rather than registered-and-refusing.

    A handler that 404s still confirms the path exists to anyone probing; a
    route FastAPI never created leaves nothing to find.
    """
    registered = {getattr(r, "path", None) for r in app.routes}
    for path in DOC_PATHS:
        assert path not in registered, f"{path} is still registered"


def test_the_doc_paths_return_404():
    client = TestClient(app)
    for path in DOC_PATHS:
        assert client.get(path).status_code == 404, f"{path} is still served"


def test_the_schema_is_not_served_under_any_name():
    """/openapi.json is the one that matters — /docs and /redoc are only
    viewers over it, so leaving the schema up would defeat removing them."""
    client = TestClient(app)
    body = client.get("/openapi.json")
    assert body.status_code == 404
    assert "paths" not in body.text


def test_the_root_endpoint_does_not_advertise_docs():
    """It used to hard-code {"docs": "/docs"}. Pointing callers at a 404 is
    worse than silence, and a null would still disclose the feature."""
    client = TestClient(app)
    body = client.get("/").json()
    assert body["status"] == "ok"
    assert "docs" not in body


def test_the_real_endpoints_still_work_with_docs_off():
    """Disabling the schema must not disturb the API itself — openapi_url=None
    also switches off FastAPI's own schema generation."""
    client = TestClient(app)
    assert client.get("/api/health").status_code == 200
    assert client.get("/api/meta").status_code == 200
    # Still auth-gated, not accidentally opened.
    assert client.get("/api/dashboard").status_code in (401, 403)


# --------------------------------------------------------------------------
# Turning it on
# --------------------------------------------------------------------------
def _routes_with_env(**env):
    """Import the app in a SUBPROCESS under the given environment.

    config.DOCS_ENABLED and the FastAPI app are both built at import time, so
    the flag cannot be flipped inside a running process without reloading half
    the package. A subprocess exercises the real production path — set the
    variable, start the app — instead of a reload that proves less.
    """
    script = textwrap.dedent("""
        import json, sys
        from server.main import app
        print(json.dumps(sorted({getattr(r, "path", "") for r in app.routes})),
              file=sys.stderr)
    """)
    child = os.environ.copy()
    child.update(env)
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True, text=True, env=child,
        cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    )
    assert result.returncode == 0, result.stderr
    import json
    return json.loads(result.stderr.strip().splitlines()[-1])


def test_docs_enabled_true_serves_them_again():
    paths = _routes_with_env(DOCS_ENABLED="true", DATABASE_URL="sqlite:///:memory:",
                             JWT_SECRET="test-only")
    for path in DOC_PATHS:
        assert path in paths, f"{path} missing with DOCS_ENABLED=true"


def test_docs_disabled_explicitly_keeps_them_off():
    paths = _routes_with_env(DOCS_ENABLED="false", DATABASE_URL="sqlite:///:memory:",
                             JWT_SECRET="test-only")
    for path in DOC_PATHS:
        assert path not in paths


# --------------------------------------------------------------------------
# The flag parser fails closed
# --------------------------------------------------------------------------
def test_the_spellings_a_dashboard_actually_receives_are_accepted(monkeypatch):
    for raw in ("1", "true", "TRUE", "True", "yes", "YES", "on", "  true  "):
        monkeypatch.setenv("DOCS_FLAG_TEST", raw)
        assert config._flag("DOCS_FLAG_TEST") is True, raw


def test_anything_else_reads_as_false(monkeypatch):
    """Including typos. A misspelled flag must not expose the schema — which is
    why this is a whitelist of true-ish values rather than a check for "false".
    """
    for raw in ("0", "false", "no", "off", "ture", "enabled", "-", "null"):
        monkeypatch.setenv("DOCS_FLAG_TEST", raw)
        assert config._flag("DOCS_FLAG_TEST") is False, raw


def test_an_unset_or_blank_flag_takes_the_default(monkeypatch):
    monkeypatch.delenv("DOCS_FLAG_TEST", raising=False)
    assert config._flag("DOCS_FLAG_TEST") is False
    assert config._flag("DOCS_FLAG_TEST", default=True) is True

    # Blank/whitespace is treated as unset, matching config._env.
    monkeypatch.setenv("DOCS_FLAG_TEST", "   ")
    assert config._flag("DOCS_FLAG_TEST") is False
