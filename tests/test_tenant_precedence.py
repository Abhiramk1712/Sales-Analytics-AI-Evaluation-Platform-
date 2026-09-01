"""
tests/test_tenant_precedence.py
===============================
A request hint may confirm the caller's tenant. It may never replace it.

Previously `resolve_tenant_context` read the company from the request header
first and the authenticated context fourth, so the authenticated value could
never win — any user could read any tenant by setting one header.
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from backend.auth.models import UserContext
from backend.auth.tenant import resolve_tenant_context
from backend.company_context import set_active_company
from backend.config import settings


def _request(path: str, headers: dict[str, str] | None = None) -> Request:
    raw_headers = [
        (k.lower().encode(), v.encode()) for k, v in (headers or {}).items()
    ]
    clean_path, _, query = path.partition("?")
    return Request({
        "type": "http",
        "http_version": "1.1",
        "method": "GET",
        "path": clean_path,
        "headers": raw_headers,
        "query_string": query.encode(),
        "client": ("test", 1234),
        "server": ("testserver", 80),
        "scheme": "http",
    })


def _authenticated(role: str, company_id: str | None) -> UserContext:
    return UserContext(role=role, company_id=company_id, is_demo=False, auth_source="token")


# ── A bound identity wins ────────────────────────────────────────────────────


def test_authenticated_company_wins_over_no_hint():
    ctx = _authenticated("sales_rep", "techo-solutions")
    tenant = resolve_tenant_context(_request("/analytics/kpis"), ctx)
    assert tenant.company_id == "techo-solutions"
    assert tenant.source == "user-context"


def test_matching_hint_is_allowed():
    ctx = _authenticated("sales_rep", "techo-solutions")
    tenant = resolve_tenant_context(
        _request("/analytics/kpis", {"X-Company-ID": "techo-solutions"}), ctx
    )
    assert tenant.company_id == "techo-solutions"


@pytest.mark.parametrize(
    "request_path,headers",
    [
        ("/analytics/kpis", {"X-Company-ID": "insurex"}),
        ("/analytics/kpis?company_id=insurex", None),
        ("/analytics/kpis?company=insurex", None),
    ],
)
def test_conflicting_hint_is_refused(request_path, headers):
    """Header, canonical query param and legacy query param are all refused."""
    ctx = _authenticated("sales_rep", "techo-solutions")
    with pytest.raises(HTTPException) as exc:
        resolve_tenant_context(_request(request_path, headers), ctx)
    assert exc.value.status_code == 403
    assert "insurex" in exc.value.detail


def test_even_an_admin_cannot_override_their_own_company_claim():
    """
    A token scoped to a company is scoped, whatever the role. Cross-tenant
    access is granted by issuing a token without a company claim, not by
    letting a privileged role ignore the one it has.
    """
    ctx = _authenticated("revops_admin", "techo-solutions")
    with pytest.raises(HTTPException) as exc:
        resolve_tenant_context(
            _request("/analytics/kpis", {"X-Company-ID": "insurex"}), ctx
        )
    assert exc.value.status_code == 403


# ── An unbound identity needs the cross-tenant permission ────────────────────


def test_unbound_operator_may_select_a_company():
    ctx = _authenticated("revops_admin", None)
    tenant = resolve_tenant_context(
        _request("/analytics/kpis", {"X-Company-ID": "insurex"}), ctx
    )
    assert tenant.company_id == "insurex"
    assert tenant.source == "request"


def test_unbound_ordinary_user_may_not_select_a_company():
    ctx = _authenticated("sales_rep", None)
    with pytest.raises(HTTPException) as exc:
        resolve_tenant_context(
            _request("/analytics/kpis", {"X-Company-ID": "insurex"}), ctx
        )
    assert exc.value.status_code == 403
    assert "manage_tenant_data" in exc.value.detail


# ── Demo mode keeps the switcher ─────────────────────────────────────────────


def test_demo_mode_may_still_switch_companies_freely():
    """The persona/company switcher is the point of demo mode."""
    ctx = UserContext(role="executive", is_demo=True)
    tenant = resolve_tenant_context(
        _request("/analytics/kpis", {"X-Company-ID": "insurex"}), ctx
    )
    assert tenant.company_id == "insurex"


def test_demo_default_is_the_last_resort():
    original = (settings.DEMO_MODE, settings.DEMO_DEFAULT_COMPANY)
    try:
        settings.DEMO_MODE = True
        settings.DEMO_DEFAULT_COMPANY = "techo-solutions"
        set_active_company(None)
        ctx = UserContext(role="executive", is_demo=True)
        tenant = resolve_tenant_context(_request("/analytics/kpis"), ctx)
        assert tenant.company_id == "techo-solutions"
        assert tenant.is_demo_fallback is True
    finally:
        settings.DEMO_MODE, settings.DEMO_DEFAULT_COMPANY = original
        set_active_company(None)
