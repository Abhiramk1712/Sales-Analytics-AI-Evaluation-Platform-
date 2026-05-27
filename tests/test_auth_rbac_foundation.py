from __future__ import annotations

import pytest
from fastapi import HTTPException

from backend.auth.dependencies import get_user_context
from backend.auth.roles import ALL_ROLES
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


def test_placeholder_bearer_claims_supported_for_scaffold() -> None:
    original_demo = settings.DEMO_MODE
    try:
        settings.DEMO_MODE = False
        ctx = get_user_context(
            authorization="Bearer demo:user_id=u-1;role=finance_admin;company_id=techo-solutions"
        )
        assert ctx.user_id == "u-1"
        assert ctx.role == "finance_admin"
        assert ctx.company_id == "techo-solutions"
        assert "approve_payouts" in ctx.permissions
    finally:
        settings.DEMO_MODE = original_demo
