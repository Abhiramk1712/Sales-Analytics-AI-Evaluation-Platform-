from __future__ import annotations

import pytest
from fastapi import HTTPException

from backend.auth.dependencies import get_user_context
from backend.auth.roles import ALL_ROLES
from backend.auth.tokens import issue_token
from backend.config import settings


def test_required_roles_present() -> None:
    expected = {
        "executive",
        "revops_admin",
        "finance_admin",
        "sales_manager",
        "sales_rep",
        "data_scientist",
        "auditor",
    }
    assert expected.issubset(ALL_ROLES)


def test_demo_mode_uses_configurable_default_role() -> None:
    original_demo = settings.DEMO_MODE
    original_default_role = settings.DEMO_DEFAULT_ROLE
    try:
        settings.DEMO_MODE = True
        settings.DEMO_DEFAULT_ROLE = "sales_manager"
        ctx = get_user_context()
        assert ctx.role == "sales_manager"
        assert ctx.is_demo is True
    finally:
        settings.DEMO_MODE = original_demo
        settings.DEMO_DEFAULT_ROLE = original_default_role


def test_production_mode_requires_authentication() -> None:
    original_demo = settings.DEMO_MODE
    try:
        settings.DEMO_MODE = False
        with pytest.raises(HTTPException) as exc:
            get_user_context(authorization=None)
        assert exc.value.status_code == 401
    finally:
        settings.DEMO_MODE = original_demo


def test_production_mode_rejects_the_unsigned_demo_token() -> None:
    """
    The old scaffold accepted `Bearer demo:role=...` verbatim — no signature,
    no expiry — which let any caller assert any role. It must now be refused
    like any other malformed token.
    """
    original_demo = settings.DEMO_MODE
    original_secret = settings.AUTH_JWT_SECRET
    try:
        settings.DEMO_MODE = False
        settings.AUTH_JWT_SECRET = "test-secret-not-for-production"
        with pytest.raises(HTTPException) as exc:
            get_user_context(
                authorization="Bearer demo:user_id=u-1;role=finance_admin;company_id=techo-solutions"
            )
        assert exc.value.status_code == 401
    finally:
        settings.DEMO_MODE = original_demo
        settings.AUTH_JWT_SECRET = original_secret


def test_production_mode_ignores_the_user_role_header() -> None:
    """
    X-User-Role was accepted as a "temporary migration bridge". A header the
    caller sets is not authentication; the verified token's role is the only
    role that counts.
    """
    original_demo = settings.DEMO_MODE
    original_secret = settings.AUTH_JWT_SECRET
    try:
        settings.DEMO_MODE = False
        settings.AUTH_JWT_SECRET = "test-secret-not-for-production"
        token = issue_token(user_id="u-1", role="sales_rep", company_id="techo-solutions")

        ctx = get_user_context(
            authorization=f"Bearer {token}",
            x_user_role="revops_admin",
            x_company_id="some-other-company",
        )

        assert ctx.role == "sales_rep", "the header must not be able to escalate the role"
        assert ctx.company_id == "techo-solutions", "the header must not switch tenant"
        assert "approve_payouts" not in ctx.permissions
    finally:
        settings.DEMO_MODE = original_demo
        settings.AUTH_JWT_SECRET = original_secret
