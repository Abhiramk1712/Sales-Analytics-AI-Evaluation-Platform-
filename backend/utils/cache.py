"""
backend/utils/cache.py
======================
Lightweight in-memory TTL cache for demo mode.
Production should replace with Redis-backed cache.
"""
from __future__ import annotations

import time
from typing import Any


class TTLCache:
    """Simple in-memory TTL cache. Not thread-safe — suitable for single-process demo."""

    def __init__(self, default_ttl: int = 60, max_size: int = 500):
        self._store: dict[str, tuple[float, Any]] = {}
        self._default_ttl = default_ttl
        self._max_size = max_size

    def get(self, key: str) -> Any | None:
        entry = self._store.get(key)
        if entry is None:
            return None
        expires_at, value = entry
        if time.monotonic() > expires_at:
            del self._store[key]
            return None
        return value

    def set(self, key: str, value: Any, ttl: int | None = None) -> None:
        if len(self._store) >= self._max_size:
            self._evict()
        self._store[key] = (time.monotonic() + (ttl or self._default_ttl), value)

    def invalidate(self, key: str) -> None:
        self._store.pop(key, None)

    def invalidate_prefix(self, prefix: str) -> None:
        keys = [k for k in self._store if k.startswith(prefix)]
        for k in keys:
            del self._store[k]

    def clear(self) -> None:
        self._store.clear()

    def _evict(self) -> None:
        now = time.monotonic()
        expired = [k for k, (exp, _) in self._store.items() if now > exp]
        for k in expired:
            del self._store[k]
        # If still over capacity, remove oldest entries
        if len(self._store) >= self._max_size:
            sorted_keys = sorted(self._store, key=lambda k: self._store[k][0])
            for k in sorted_keys[: len(self._store) - self._max_size + 1]:
                del self._store[k]


def make_cache_key(prefix: str, *, company: str = "", role: str = "", **params: Any) -> str:
    parts = [prefix, company, role]
    for k in sorted(params):
        v = params[k]
        if v is not None and v != "":
            parts.append(f"{k}={v}")
    return ":".join(parts)


# No module-level cache instance here on purpose. One existed (`dashboard_cache
# = TTLCache(...)`) but nothing in the app ever imported it — only this
# module's own test exercised TTLCache/make_cache_key directly. A cache
# instance nobody reads from or writes to isn't caching, it's a global that
# implies caching is happening. When a real call site wires this in, that's
# where the instance should live (or become Redis-backed, per the TODO these
# classes were already written against) — scoped to what it caches.
