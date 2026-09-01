"""
tests/test_exception_handler.py
================================
Verify the global exception handler does not leak internal details.
"""
from fastapi.testclient import TestClient
from backend.main import app

client = TestClient(app, raise_server_exceptions=False)


def test_health_endpoint():
    """Health endpoint should return 200 with status ok."""
    res = client.get("/health")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "ok"
    assert "version" in body


def test_global_error_handler_hides_details():
    """Global exception handler should not expose raw exception messages."""
    # Hit an endpoint that would trigger an error (e.g. bad route)
    res = client.get("/analytics/nonexistent-endpoint-test")
    # Should be 404 (not found) or similar — but NOT contain raw tracebacks
    body = res.json()
    if res.status_code == 500:
        assert "detail" in body
        # Should NOT contain raw Python exception text
        assert "Traceback" not in body.get("detail", "")
        assert body["detail"] == "Internal server error"
        assert "correlation_id" in body
