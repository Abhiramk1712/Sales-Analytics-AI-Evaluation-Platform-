from __future__ import annotations

from starlette.requests import Request
from sqlalchemy import select

from backend.auth.models import UserContext
from backend.auth.tenant import apply_company_scope, resolve_tenant_context
from backend.company_context import get_active_company, set_active_company
from backend.config import settings
from backend.models import Rep


def _request(path: str, headers: dict[str, str] | None = None) -> Request:
    raw_headers = []
    for key, value in (headers or {}).items():
        raw_headers.append((key.lower().encode("utf-8"), value.encode("utf-8")))

    if "?" in path:
        clean_path, query = path.split("?", 1)
        query_bytes = query.encode("utf-8")
    else:
        clean_path = path
        query_bytes = b""

    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "GET",
        "path": clean_path,
        "headers": raw_headers,
        "query_string": query_bytes,
        "client": ("test", 1234),
        "server": ("testserver", 80),
        "scheme": "http",
    }
    return Request(scope)


def test_header_company_id_takes_precedence() -> None:
    req = _request("/analytics/kpis?company_id=query-co", headers={"X-Company-ID": "header-co"})
    ctx = UserContext(role="executive", is_demo=True)
    tenant = resolve_tenant_context(req, ctx)
    assert tenant.company_id == "header-co"
    assert tenant.source == "request"


def test_user_context_company_fallback() -> None:
    req = _request("/analytics/kpis")
    ctx = UserContext(role="executive", company_id="user-co", is_demo=True)
    tenant = resolve_tenant_context(req, ctx)
    assert tenant.company_id == "user-co"
    assert tenant.source == "user-context"


def test_active_company_context_fallback() -> None:
    original = get_active_company()
    try:
        set_active_company("active-co")
        req = _request("/analytics/kpis")
        ctx = UserContext(role="executive", is_demo=True)
        tenant = resolve_tenant_context(req, ctx)
        assert tenant.company_id == "active-co"
        assert tenant.source == "active-context"
    finally:
        set_active_company(original)


def test_demo_default_company_fallback() -> None:
    original_demo = settings.DEMO_MODE
    original_default_company = settings.DEMO_DEFAULT_COMPANY
    original_active = get_active_company()
    try:
        set_active_company(None)
        settings.DEMO_MODE = True
        settings.DEMO_DEFAULT_COMPANY = "demo-co"
        req = _request("/analytics/kpis")
        ctx = UserContext(role="executive", is_demo=True)
        tenant = resolve_tenant_context(req, ctx)
        assert tenant.company_id == "demo-co"
        assert tenant.is_demo_fallback is True
    finally:
        set_active_company(original_active)
        settings.DEMO_MODE = original_demo
        settings.DEMO_DEFAULT_COMPANY = original_default_company


def test_apply_company_scope_noop_without_column() -> None:
    query = select(Rep)
    scoped = apply_company_scope(query, Rep, "co-1")
    assert scoped is query
