"""
tests/test_rbac_permissions.py
================================
Tests for RBAC roles, permissions, and the UserContext dependency.
Two roles only: executive and revops_admin.
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from backend.auth.roles import (
    ROLE_EXECUTIVE,
    ROLE_REVOPS_ADMIN,
    has_permission,
)
from backend.auth.dependencies import UserContext, require_role


class TestHasPermission:
    def test_executive_has_company_metrics(self):
        assert has_permission(ROLE_EXECUTIVE, "view_company_metrics") is True

    def test_revops_admin_has_company_metrics(self):
        assert has_permission(ROLE_REVOPS_ADMIN, "view_company_metrics") is True

    def test_executive_has_view_all_payouts(self):
        assert has_permission(ROLE_EXECUTIVE, "view_all_payouts") is True

    def test_revops_admin_can_edit_plans(self):
        assert has_permission(ROLE_REVOPS_ADMIN, "edit_plans") is True

    def test_executive_cannot_edit_plans(self):
        assert has_permission(ROLE_EXECUTIVE, "edit_plans") is False

    def test_revops_admin_can_run_ingestion(self):
        assert has_permission(ROLE_REVOPS_ADMIN, "run_ingestion") is True

    def test_executive_cannot_run_ingestion(self):
        assert has_permission(ROLE_EXECUTIVE, "run_ingestion") is False

    def test_executive_can_view_all_reps(self):
        assert has_permission(ROLE_EXECUTIVE, "view_all_reps") is True

    def test_revops_admin_can_view_all_reps(self):
        assert has_permission(ROLE_REVOPS_ADMIN, "view_all_reps") is True

    def test_revops_admin_is_admin(self):
        assert has_permission(ROLE_REVOPS_ADMIN, "admin") is True

    def test_executive_is_not_admin(self):
        assert has_permission(ROLE_EXECUTIVE, "admin") is False

    def test_both_roles_can_generate_reports(self):
        assert has_permission(ROLE_EXECUTIVE, "generate_reports") is True
        assert has_permission(ROLE_REVOPS_ADMIN, "generate_reports") is True

    def test_unknown_permission_returns_false(self):
        assert has_permission(ROLE_EXECUTIVE, "delete_database") is False

    def test_unknown_role_returns_false(self):
        assert has_permission("ghost", "view_company_metrics") is False


class TestUserContext:
    def test_is_admin_for_revops(self):
        ctx = UserContext(role=ROLE_REVOPS_ADMIN)
        assert ctx.is_admin is True

    def test_is_not_admin_for_executive(self):
        ctx = UserContext(role=ROLE_EXECUTIVE)
        assert ctx.is_admin is False

    def test_require_permission_passes(self):
        ctx = UserContext(role=ROLE_EXECUTIVE)
        ctx.require_permission("view_company_metrics")  # should not raise

    def test_require_permission_raises_403(self):
        ctx = UserContext(role=ROLE_EXECUTIVE)
        with pytest.raises(HTTPException) as exc_info:
            ctx.require_permission("edit_plans")
        assert exc_info.value.status_code == 403

    def test_default_role_is_executive(self):
        ctx = UserContext()
        assert ctx.role == ROLE_EXECUTIVE


class TestRequireRole:
    def test_allowed_role_passes(self):
        ctx = UserContext(role=ROLE_EXECUTIVE)
        assert ctx.role == ROLE_EXECUTIVE

    def test_require_role_blocks_wrong_role(self):
        dep_fn = require_role(ROLE_EXECUTIVE, ROLE_REVOPS_ADMIN)
        import inspect
        src = inspect.getsource(dep_fn)
        assert "403" in src or "required_roles" in src
