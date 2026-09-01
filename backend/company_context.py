"""Company context state for company-scoped API alignment."""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any


_active_company: str | None = None
_context_lock = asyncio.Lock()
_company_load_in_progress = False


def get_active_company() -> str | None:
    return _active_company


def set_active_company(company_name: str | None) -> None:
    global _active_company
    normalized = (company_name or "").strip()
    _active_company = normalized or None


def is_company_load_in_progress() -> bool:
    return _company_load_in_progress


async def wait_for_company_load_completion() -> None:
    """Wait until any in-flight company dataset load has finished."""
    if _company_load_in_progress or _context_lock.locked():
        async with _context_lock:
            return


async def load_company_into_context(
    company_name: str,
    *,
    force_reload: bool = False,
    loader: Callable[[str], Awaitable[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    """
    Load a company dataset under a shared lock and update active context.

    Returns loader row-count metadata when a load executes, else `{}` when no
    reload is needed.
    """
    global _active_company, _company_load_in_progress

    normalized = (company_name or "").strip()
    if not normalized:
        return {}

    if not force_reload and _active_company == normalized:
        return {}

    async with _context_lock:
        if not force_reload and _active_company == normalized:
            return {}

        # Lazy import to avoid circular dependencies at module import time.
        if loader is None:
            from backend.data_generator import load_company_dataset as _default_loader

            use_loader = _default_loader
        else:
            use_loader = loader

        _company_load_in_progress = True
        try:
            # Clear in-memory caches scoped to previous company
            try:
                from backend.payout.audit_trail_service import clear_store as _clear_payout_store
                _clear_payout_store()
            except ImportError:
                pass
            counts = await use_loader(normalized)
            _active_company = normalized
            return counts or {}
        finally:
            _company_load_in_progress = False


async def ensure_company_loaded(company_name: str) -> bool:
    """
    Ensure DB context matches requested company.

    Returns True if a load was executed, False if context already matched.
    """
    normalized = (company_name or "").strip()
    if not normalized:
        return False

    counts = await load_company_into_context(normalized, force_reload=False)
    return bool(counts)