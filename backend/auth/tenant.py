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


#: Permission that lets a caller act outside a single company. Held by
#: revops_admin, which is the operator role that loads and switches datasets.
PERM_CROSS_TENANT = "manage_tenant_data"


def resolve_tenant_context(request: Request, user_ctx: UserContext) -> TenantContext:
    """
    Resolve the active company for this request.

    The authenticated company wins. A request hint (header or query param) is
    caller-controlled, so it may only *confirm* the company the caller is
    already bound to — never replace it. Previously the hint took precedence
    over the authenticated context, which meant any user could read any
    tenant's data by setting one header.

    Resolution order:
    1) `company_id` claim on the authenticated user — authoritative when present.
       A conflicting request hint is a cross-tenant attempt and is refused.
    2) For a caller not bound to a company (demo mode, or a token carrying no
       `company_id`), the request hint — but in production that requires the
       cross-tenant permission, so an ordinary user with no company claim
       cannot pick one freely.
    3) The active in-process company.
    4) DEMO_DEFAULT_COMPANY, in demo mode only.
    """
    hint = _normalize_company(extract_company_hint(request))
    bound = _normalize_company(user_ctx.company_id)

    if bound:
        if hint and hint != bound:
            raise HTTPException(
                status_code=403,
                detail=(
                    f"Not authorized for company '{hint}'. "
                    f"This identity is scoped to '{bound}'."
                ),
            )
        return TenantContext(company_id=bound, source="user-context")

    if hint:
        if not user_ctx.is_demo and not user_ctx.can(PERM_CROSS_TENANT):
            raise HTTPException(
                status_code=403,
                detail=(
                    "Selecting a company requires the 'manage_tenant_data' permission. "
                    "Ordinary identities are scoped by their token's company_id claim."
                ),
            )
        return TenantContext(company_id=hint, source="request")

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


class ModelNotTenantScopedError(RuntimeError):
    """Raised when a model without `company_id` is asked to be tenant-scoped."""


def apply_company_scope(query: Select[Any], model: Any, company_id: str) -> Select[Any]:
    """
    Restrict `query` to one company.

    Raises when `model` has no `company_id` column. It used to return the query
    untouched in that case — which meant calling this function on an unmigrated
    model produced an unscoped query that *looked* scoped at the call site. A
    guard that silently does nothing is worse than no guard: it reads as
    protection everywhere it appears.

    Every domain model carries `company_id` as of the tenancy migration, so this
    should only fire on a genuinely non-tenant table, where the caller should
    not be scoping in the first place.
    """
    if not hasattr(model, "company_id"):
        raise ModelNotTenantScopedError(
            f"{getattr(model, '__name__', model)} has no company_id column, so it "
            "cannot be tenant-scoped. Either add the column or do not call "
            "apply_company_scope on it."
        )
    return query.where(getattr(model, "company_id") == company_id)
