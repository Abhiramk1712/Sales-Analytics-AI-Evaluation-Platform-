"""
backend/auth/dependencies.py
=============================
FastAPI dependencies for RBAC with demo and production scaffolds.

Headers used (all optional in demo mode):
  X-User-Id      — user UUID
  X-User-Role    — one of enterprise roles
  X-Team-Id      — team UUID
  X-Territory-Id — territory UUID
  X-Company-Id   — active tenant/company scope

Production scaffold behavior:
  - Requires Authorization: Bearer <token>
  - Accepts explicit X-User-Role only as temporary migration bridge
"""
from __future__ import annotations

from typing import Optional

from fastapi import Depends, Header, HTTPException

from backend.auth.models import UserContext
from backend.auth.roles import ALL_ROLES, ROLE_EXECUTIVE, has_permission, permissions_for_role
from backend.config import settings


def _coerce_optional_header(value: object) -> Optional[str]:
    """Return normalized header string values; ignore FastAPI marker objects."""
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _parse_bearer_token(authorization: Optional[str]) -> Optional[str]:
    if not authorization:
        return None

    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise HTTPException(status_code=401, detail="Invalid Authorization header. Expected: Bearer <token>")

    return token.strip()


def _parse_placeholder_claims(token: str | None) -> dict[str, str]:
    """
    Temporary scaffold parser for local development in production-mode tests.

    Supported shape:
      Authorization: Bearer demo:user_id=...;role=...;company_id=...
    """
    if not token:
        return {}
    if not token.startswith("demo:"):
        return {}

    payload = token[len("demo:") :]
    claims: dict[str, str] = {}
    for part in payload.split(";"):
        key, _, value = part.partition("=")
        key = key.strip()
        value = value.strip()
        if key and value:
            claims[key] = value
    return claims


def _normalize_role(role: object) -> str:
    normalized = (_coerce_optional_header(role) or "").strip().lower()
    return normalized


def _resolve_role(x_user_role: Optional[str], claims: dict[str, str]) -> str:
    if settings.DEMO_MODE:
        role = _normalize_role(x_user_role or claims.get("role") or settings.DEMO_DEFAULT_ROLE or ROLE_EXECUTIVE)
        if role not in ALL_ROLES:
            role = _normalize_role(settings.DEMO_DEFAULT_ROLE or ROLE_EXECUTIVE)
        if role not in ALL_ROLES:
            role = ROLE_EXECUTIVE
        return role

    role = _normalize_role(x_user_role or claims.get("role"))
    if not role:
        raise HTTPException(
            status_code=401,
            detail="Production mode requires an authenticated role claim. Provide a validated token or X-User-Role during migration.",
        )
    if role not in ALL_ROLES:
        raise HTTPException(status_code=403, detail=f"Unsupported role '{role}'")
    return role



def get_user_context(
    authorization: Optional[str] = Header(None, alias="Authorization"),
    x_user_id: Optional[str] = Header(None, alias="X-User-Id"),
    x_user_role: Optional[str] = Header(None, alias="X-User-Role"),
    x_team_id: Optional[str] = Header(None, alias="X-Team-Id"),
    x_territory_id: Optional[str] = Header(None, alias="X-Territory-Id"),
    x_company_id: Optional[str] = Header(None, alias="X-Company-Id"),
) -> UserContext:
    """Extract user context from headers/token with safe defaults in demo mode."""
    auth_header = _coerce_optional_header(authorization)
    user_id_header = _coerce_optional_header(x_user_id)
    role_header = _coerce_optional_header(x_user_role)
    team_id_header = _coerce_optional_header(x_team_id)
    territory_id_header = _coerce_optional_header(x_territory_id)
    company_id_header = _coerce_optional_header(x_company_id)

    token = _parse_bearer_token(auth_header)
    claims = _parse_placeholder_claims(token)

    if not settings.DEMO_MODE and token is None:
        raise HTTPException(
            status_code=401,
            detail="Authentication required in production mode. Supply Authorization: Bearer <token>",
        )

    role = _resolve_role(x_user_role=role_header, claims=claims)
    user_id = user_id_header or claims.get("user_id") or ("demo-user" if settings.DEMO_MODE else None)

    return UserContext(
        user_id=user_id,
        role=role,
        team_id=team_id_header,
        territory_id=territory_id_header,
        company_id=company_id_header or claims.get("company_id"),
        permissions=permissions_for_role(role),
        auth_source="demo" if settings.DEMO_MODE else "token",
        is_demo=settings.DEMO_MODE,
    )


def require_permission(permission: str):
    """FastAPI dependency factory enforcing a permission string."""

    def _check(ctx: UserContext = Depends(get_user_context)) -> UserContext:
        if not has_permission(ctx.role, permission):
            raise HTTPException(
                status_code=403,
                detail=f"Role '{ctx.role}' does not have permission '{permission}'",
            )
        return ctx

    return _check


def require_role(*required_roles: str):
    """
    FastAPI dependency factory.
    Usage: Depends(require_role("executive", "revops_admin"))
    """
    normalized = {_normalize_role(r) for r in required_roles if r}
    if not normalized:
        raise ValueError("require_role expects at least one role")

    def _check(ctx: UserContext = Depends(get_user_context)) -> UserContext:
        if ctx.role not in normalized:
            raise HTTPException(
                status_code=403,
                detail=f"Requires one of roles: {sorted(normalized)}. Current role: {ctx.role}",
            )
        return ctx

    return _check
