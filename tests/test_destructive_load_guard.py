"""
tests/test_destructive_load_guard.py
=====================================
Verify that destructive database operations require explicit flags.

The fixture below restores `backend.config` and the cached engine on teardown.
It previously did not: `patch.dict` put the env back, but the reloaded
`settings` object and the engine cached in `backend.database` kept the test's
placeholder DATABASE_URL, so every later test in the session inherited a
database that does not exist. That is how a passing suite turns into an
order-dependent one.
"""
import importlib

import pytest
from unittest.mock import patch
from fastapi.testclient import TestClient

# revops_admin is the role that holds `run_ingestion`; these endpoints are
# operator endpoints and are expected to require it.
ADMIN = {"X-User-Role": "revops_admin"}


@pytest.fixture
def client():
    """Test client with ALLOW_DESTRUCTIVE_LOAD=false, fully restored afterwards."""
    import backend.config
    import backend.database

    saved_engine = backend.database._engine
    saved_factory = backend.database._async_session_factory

    with patch.dict("os.environ", {
        "ALLOW_DESTRUCTIVE_LOAD": "false",
        "DEMO_MODE": "true",
        "DATABASE_URL": "postgresql+asyncpg://localhost:5432/test_placeholder",
    }):
        importlib.reload(backend.config)
        # The engine is cached on first use; drop it so this test's URL is not
        # baked into a connection pool that outlives the fixture.
        backend.database._engine = None
        backend.database._async_session_factory = None

        from backend.main import app
        yield TestClient(app, raise_server_exceptions=False)

    # Restore the module-level settings the rest of the session reads from.
    importlib.reload(backend.config)
    backend.database._engine = saved_engine
    backend.database._async_session_factory = saved_factory


def test_intelligent_load_rejects_destructive_without_flag(client):
    """Destructive load should be rejected when ALLOW_DESTRUCTIVE_LOAD is false."""
    res = client.post("/ingestion/intelligent-load", headers=ADMIN, json={
        "source_dir": "companies/techo-solutions",
        "company_name": "test-co",
        "reset_database": True,
        "load_mode": "full_reload",
    })
    assert res.status_code == 403
    assert "destructive" in res.json().get("detail", "").lower()


def test_intelligent_load_allows_non_destructive(client):
    """Non-destructive load (append mode) should not be blocked by the guard."""
    res = client.post("/ingestion/intelligent-load", headers=ADMIN, json={
        "source_dir": "companies/does-not-exist",
        "company_name": "test-co",
        "reset_database": False,
        "load_mode": "append",
    })
    # Should fail for other reasons (dir not found), not the destructive guard
    assert res.status_code != 403


def test_load_endpoints_require_the_ingestion_permission(client):
    """
    An identity without `run_ingestion` cannot reach the loader at all — the
    guard on the flag is the second line of defence, not the first.
    """
    res = client.post(
        "/ingestion/intelligent-load",
        headers={"X-User-Role": "sales_rep"},
        json={
            "source_dir": "companies/techo-solutions",
            "company_name": "test-co",
            "reset_database": False,
            "load_mode": "append",
        },
    )
    assert res.status_code == 403
    assert "run_ingestion" in res.json().get("detail", "")


def test_source_dir_outside_the_configured_root_is_refused(client):
    """
    source_dir is caller-supplied. An unconfined value let a caller walk the
    host filesystem and read directory contents back out of the response.
    """
    res = client.post("/ingestion/inspect", headers=ADMIN, json={
        "source_dir": "/etc",
        "company_name": "test-co",
    })
    assert res.status_code == 400
    assert "must be inside" in res.json().get("detail", "")


def test_source_dir_traversal_is_refused(client):
    res = client.post("/ingestion/inspect", headers=ADMIN, json={
        "source_dir": "../../../../etc",
        "company_name": "test-co",
    })
    assert res.status_code == 400
