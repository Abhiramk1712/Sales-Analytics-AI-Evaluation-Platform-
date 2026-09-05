"""
Which company's data was most recently loaded.

This module used to be the tenancy mechanism: a process-global `_active_company`,
and `ensure_company_loaded` on the request path that reloaded the entire database
whenever a request named a different company. Both are gone. Tenancy now lives in
`backend/tenancy.py` (a request-scoped ContextVar) and is enforced in
`backend/tenant_guard.py` (session-level filtering).

What remains is a marker — the last company an explicit load ran for — plus the
lock that keeps two concurrent loads of the same company from interleaving their
delete-and-insert. The marker is a convenience for the UI and a fallback for
readers outside a request; it is no longer what makes a query safe.
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from backend.tenancy import get_current_tenant

_last_loaded_company: str | None = None
_load_lock = asyncio.Lock()


def get_active_company() -> str | None:
    """
    The company this caller should be reading.

    Prefers the tenant bound to the current request, so callers that were written
    against the old global automatically became request-scoped when the
    ContextVar was introduced, rather than each needing to be found and edited.
    Falls back to the last explicitly loaded company for work outside a request.
    """
    return get_current_tenant() or _last_loaded_company


def get_last_loaded_company() -> str | None:
    """The marker itself, ignoring any bound request tenant."""
    return _last_loaded_company


def set_active_company(company_name: str | None) -> None:
    """Record which company was most recently loaded."""
    global _last_loaded_company
    normalized = (company_name or "").strip()
    _last_loaded_company = normalized or None


async def load_company_into_context(
    company_name: str,
    *,
    force_reload: bool = False,
    loader: Callable[[str], Awaitable[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    """
    Load a company's dataset and record it as the last loaded.

    The load is scoped to that company — it replaces that company's rows and
    leaves every other tenant intact — so this is no longer destructive to
    anything but the company named. The lock remains because two concurrent
    loads of the *same* company would still interleave their delete and insert.

    Returns the loader's row counts, or `{}` when the load was skipped.
    """
    normalized = (company_name or "").strip()
    if not normalized:
        return {}

    if not force_reload and _last_loaded_company == normalized:
        return {}

    async with _load_lock:
        if not force_reload and _last_loaded_company == normalized:
            return {}

        if loader is None:
            # Lazy import to avoid a cycle at module import time.
            from backend.data_generator import load_company_dataset as _default_loader

            use_loader = _default_loader
        else:
            use_loader = loader

        # In-memory caches keyed to the company being (re)loaded. Scoped to
        # `normalized` -- clear_store() with no argument would wipe every
        # *other* resident company's audit trail too (approvals, locks,
        # corrections), even though records are already keyed by company_id
        # and loading one company leaves every other tenant's rows intact.
        try:
            from backend.payout.audit_trail_service import clear_store as _clear_payout_store

            _clear_payout_store(company_id=normalized)
        except ImportError:
            pass

        counts = await use_loader(normalized)
        set_active_company(normalized)
        return counts or {}
