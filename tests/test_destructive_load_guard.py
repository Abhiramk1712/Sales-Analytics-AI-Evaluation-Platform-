"""
tests/test_destructive_load_guard.py
=====================================
Verify that destructive database operations require explicit flags.
"""
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    """Create a test client with ALLOW_DESTRUCTIVE_LOAD=false."""
    with patch.dict("os.environ", {
        "ALLOW_DESTRUCTIVE_LOAD": "false",
        "DEMO_MODE": "true",
        "DATABASE_URL": "postgresql+asyncpg://localhost:5432/test_placeholder",
    }):
        # Re-import to pick up env override
        import importlib
        import backend.config
        importlib.reload(backend.config)
        from backend.main import app
        yield TestClient(app, raise_server_exceptions=False)


def test_intelligent_load_rejects_destructive_without_flag(client):
    """Destructive load should be rejected when ALLOW_DESTRUCTIVE_LOAD is false."""
    res = client.post("/ingestion/intelligent-load", json={
        "source_dir": "/tmp/test",
        "company_name": "test-co",
        "reset_database": True,
        "load_mode": "full_reload",
    })
    assert res.status_code == 403
    assert "destructive" in res.json().get("detail", "").lower()


def test_intelligent_load_allows_non_destructive(client):
    """Non-destructive load (append mode) should not be blocked by the guard."""
    res = client.post("/ingestion/intelligent-load", json={
        "source_dir": "/tmp/nonexistent",
        "company_name": "test-co",
        "reset_database": False,
        "load_mode": "append",
    })
    # Should fail for other reasons (dir not found), not the destructive guard
    assert res.status_code != 403
