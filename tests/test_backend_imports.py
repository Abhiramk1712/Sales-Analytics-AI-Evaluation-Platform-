"""
tests/test_backend_imports.py
==============================
Verify that backend modules can be imported without a live database connection.
This ensures lazy engine initialization works properly.
"""
import importlib


def test_import_backend_config():
    """backend.config should import without DB."""
    mod = importlib.import_module("backend.config")
    assert hasattr(mod, "settings")


def test_import_backend_database():
    """backend.database should import without creating an engine."""
    mod = importlib.import_module("backend.database")
    assert hasattr(mod, "get_engine")
    assert hasattr(mod, "get_session_factory")
    assert hasattr(mod, "get_db")
    assert hasattr(mod, "Base")
    # Engine should NOT be created at import time
    assert mod._engine is None


def test_import_backend_models():
    """backend.models should import without a live DB."""
    mod = importlib.import_module("backend.models")
    assert hasattr(mod, "Rep")
    assert hasattr(mod, "Deal")
    assert hasattr(mod, "Revenue")
    assert hasattr(mod, "PayoutRecord")
    assert hasattr(mod, "PayoutConfiguration")
    assert hasattr(mod, "JobStatus")


def test_import_backend_payout_engine():
    """backend.payout.engine should import without DB."""
    mod = importlib.import_module("backend.payout.engine")
    assert hasattr(mod, "PayoutEngine")
    assert hasattr(mod, "PayoutConfig")
    assert hasattr(mod, "DEFAULT_PAYOUT_CONFIG")


def test_import_backend_company_context():
    """backend.company_context should import without DB."""
    mod = importlib.import_module("backend.company_context")
    assert hasattr(mod, "get_active_company")
    assert hasattr(mod, "set_active_company")


def test_import_backend_auth():
    """backend.auth modules should import without DB."""
    mod = importlib.import_module("backend.auth.dependencies")
    assert hasattr(mod, "get_user_context")
    mod2 = importlib.import_module("backend.auth.roles")
    assert hasattr(mod2, "ALL_ROLES")
