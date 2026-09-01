"""
backend/auth/dependencies.py
=============================
FastAPI dependencies for RBAC.

There are exactly two modes, and they do not overlap.

Demo mode (DEMO_MODE=true) — for local exploration and the walkthrough.
Identity comes from headers, which means the caller chooses their own role.
That is the point of the demo persona switcher, and it is safe only because
demo mode is not an authenticated mode at all:

  X-User-Id      — user identifier
  X-User-Role    — one of the enterprise roles
  X-Team-Id      — team UUID
  X-Territory-Id — territory UUID
  X-Company-Id   — tenant/company scope

Production mode (DEMO_MODE=false). Identity comes from a signed JWT and
nothing else. `X-User-Role` and the other identity headers are ignored
entirely — not "accepted during migration", ignored. Accepting a role header
in production was a complete authorization bypass: any caller could assert
`X-User-Role: revops_admin` and approve payouts. Tenant scope comes from the
token's `company_id` claim; see auth/tenant.py for how a request may narrow it.
"""
from __future__ import annotations

from typing import Optional

from fastapi import Depends, Header, HTTPException

from backend.auth.models import UserContext
from backend.auth.roles import ALL_ROLES, ROLE_EXECUTIVE, has_permission, permissions_for_role
from backend.auth.tokens import claims_to_context_fields, decode_token
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


def _normalize_role(role: object) -> str:
    normalized = (_coerce_optional_header(role) or "").strip().lower()
    return normalized


def _resolve_demo_role(x_user_role: Optional[str]) -> str:
    """Demo mode only: the caller picks a persona, falling back to the default."""
    role = _normalize_role(x_user_role or settings.DEMO_DEFAULT_ROLE or ROLE_EXECUTIVE)
    if role not in ALL_ROLES:
        role = _normalize_role(settings.DEMO_DEFAULT_ROLE or ROLE_EXECUTIVE)
    if role not in ALL_ROLES:
        role = ROLE_EXECUTIVE
    return role


def _resolve_verified_role(claims: dict[str, str]) -> str:
    """Production mode only: the role comes from the verified token, or not at all."""
    role = _normalize_role(claims.get("role"))
    if not role:
        raise HTTPException(
            status_code=401,
            detail="Token does not carry a 'role' claim.",
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
    """
    Build the request's UserContext.

    In demo mode this reads headers; in production mode it reads a verified
    token and ignores every identity header. The two paths are kept visibly
    separate so it is never ambiguous which one is in force.
    """
    auth_header = _coerce_optional_header(authorization)
    token = _parse_bearer_token(auth_header)

    if settings.DEMO_MODE:
        role = _resolve_demo_role(_coerce_optional_header(x_user_role))
        return UserContext(
            user_id=_coerce_optional_header(x_user_id) or "demo-user",
            role=role,
            team_id=_coerce_optional_header(x_team_id),
            territory_id=_coerce_optional_header(x_territory_id),
            company_id=_coerce_optional_header(x_company_id),
            permissions=permissions_for_role(role),
            auth_source="demo",
            is_demo=True,
        )

    if token is None:
        raise HTTPException(
            status_code=401,
            detail="Authentication required. Supply Authorization: Bearer <token>",
        )

    # Identity headers are deliberately not consulted below. A verified token is
    # the only source of identity in production.
    claims = claims_to_context_fields(decode_token(token))
    role = _resolve_verified_role(claims)

    return UserContext(
        user_id=claims.get("user_id"),
        role=role,
        team_id=claims.get("team_id"),
        territory_id=claims.get("territory_id"),
        company_id=claims.get("company_id"),
        permissions=permissions_for_role(role),
        auth_source="token",
        is_demo=False,
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
