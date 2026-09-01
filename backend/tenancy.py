"""
backend/tenancy.py
==================
Request-scoped tenant identity.

This replaces the process-global `_active_company` in company_context.py as the
way a request knows which tenant it is serving. The difference matters: a module
global is shared by every concurrent request, so two users on different
companies overwrite each other's context. A ContextVar is bound to the task
handling one request, so concurrent requests for different tenants cannot see
each other's value.

That is the first half of moving from swap-based tenancy to query-scoped
tenancy. The second half — putting `company_id` into every query — is what
`apply_company_scope` in auth/tenant.py exists for, and is migrated table by
table. Until a model carries `company_id`, that function now raises rather than
silently returning an unscoped query, so an unmigrated table is a loud error
instead of a quiet cross-tenant read.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar, Token
from typing import Iterator, Optional

#: The tenant this request belongs to. Never read this directly from business
#: logic — use require_current_tenant(), so a missing tenant is an error at the
#: point of use rather than a silently unscoped query.
_current_tenant: ContextVar[Optional[str]] = ContextVar("current_tenant", default=None)


class TenantNotSetError(RuntimeError):
    """Raised when tenant-scoped work is attempted with no tenant bound."""


def get_current_tenant() -> Optional[str]:
    """Return the tenant bound to this request, or None."""
    return _current_tenant.get()


def require_current_tenant() -> str:
    """
    Return the bound tenant, or raise.

    Anything that reads or writes tenant data should call this rather than
    `get_current_tenant()`. Failing loudly on a missing tenant is the whole
    point: the previous model's failure mode was to carry on with whatever
    company happened to be loaded process-wide.
    """
    tenant = _current_tenant.get()
    if not tenant:
        raise TenantNotSetError(
            "No tenant bound to this request. Tenant-scoped work requires "
            "set_current_tenant() — usually via the request middleware."
        )
    return tenant


def set_current_tenant(company_id: Optional[str]) -> Token:
    """
    Bind a tenant to the current context and return the reset token.

    Prefer `tenant_scope()` where the binding has a clear beginning and end.
    """
    normalized = (company_id or "").strip() or None
    return _current_tenant.set(normalized)


def reset_current_tenant(token: Token) -> None:
    """Restore whatever tenant was bound before the matching set."""
    _current_tenant.reset(token)


@contextmanager
def tenant_scope(company_id: Optional[str]) -> Iterator[Optional[str]]:
    """
    Bind a tenant for the duration of a block.

        with tenant_scope("acme"):
            ...  # every query in here is acme's

    The binding is restored on exit even if the block raises, and it is visible
    only to this task — a concurrent request in another task keeps its own.
    """
    token = set_current_tenant(company_id)
    try:
        yield get_current_tenant()
    finally:
        reset_current_tenant(token)
