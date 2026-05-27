"""Tenant/company scoping helpers for enterprise-safe request handling."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fastapi import Depends, HTTPException, Request
from sqlalchemy.sql import Select

from backend.auth.dependencies import get_user_context
from backend.auth.models import UserContext
from backend.company_context import get_active_company
from backend.config import settings


@dataclass
class TenantContext:
    company_id: str
    source: str
    is_demo_fallback: bool = False


def extract_company_hint(request: Request) -> str | None:
    """Extract company identifier from request header/query without enforcing auth."""
    return (
        request.headers.get("X-Company-ID")
        or request.headers.get("X-Company-Id")
        or request.query_params.get("company_id")
        or request.query_params.get("company")
    )


def _normalize_company(company_id: str | None) -> str | None:
    normalized = (company_id or "").strip()
    return normalized or None


def resolve_tenant_context(request: Request, user_ctx: UserContext) -> TenantContext:
    """
    Resolve active company from request/query/auth context.

    Priority order:
    1) X-Company-ID request header
    2) query param company_id
    3) query param company (legacy)
    4) authenticated user context company_id
    5) active in-process company context
    6) DEMO_DEFAULT_COMPANY when DEMO_MODE=true
    """
    company = _normalize_company(extract_company_hint(request))
    if company:
        return TenantContext(company_id=company, source="request")

    company = _normalize_company(user_ctx.company_id)
    if company:
        return TenantContext(company_id=company, source="user-context")

    active = _normalize_company(get_active_company())
    if active:
        return TenantContext(company_id=active, source="active-context")

    demo_company = _normalize_company(settings.DEMO_DEFAULT_COMPANY)
    if settings.DEMO_MODE and demo_company:
        return TenantContext(company_id=demo_company, source="demo-default", is_demo_fallback=True)

    raise HTTPException(
        status_code=400,
        detail="Company context is required. Provide X-Company-ID or company_id query parameter.",
    )


def get_tenant_context(
    request: Request,
    user_ctx: UserContext = Depends(get_user_context),
) -> TenantContext:
    return resolve_tenant_context(request=request, user_ctx=user_ctx)


def get_current_company_id(tenant_ctx: TenantContext = Depends(get_tenant_context)) -> str:
    return tenant_ctx.company_id


def apply_company_scope(query: Select[Any], model: Any, company_id: str) -> Select[Any]:
    """
    Apply company scoping when model contains a company_id column.

    This function intentionally no-ops for models without company_id so that
    existing single-tenant schemas keep working until full tenant migration.
    """
    if hasattr(model, "company_id"):
        return query.where(getattr(model, "company_id") == company_id)
    return query
