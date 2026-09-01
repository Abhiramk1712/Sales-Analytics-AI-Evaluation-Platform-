"""
tests/test_cache_utils.py
==========================
Verify in-memory TTL cache works correctly.
"""
import time
from backend.utils.cache import TTLCache, make_cache_key


def test_cache_set_and_get():
    cache = TTLCache(default_ttl=60)
    cache.set("key1", {"data": 42})
    assert cache.get("key1") == {"data": 42}


def test_cache_miss():
    cache = TTLCache(default_ttl=60)
    assert cache.get("nonexistent") is None


def test_cache_ttl_expiry():
    cache = TTLCache(default_ttl=0)  # 0 second TTL
    cache.set("expire_me", "value", ttl=0)
    # After tiny sleep, entry should be expired
    time.sleep(0.01)
    assert cache.get("expire_me") is None


def test_cache_invalidate():
    cache = TTLCache(default_ttl=60)
    cache.set("key1", "val1")
    cache.invalidate("key1")
    assert cache.get("key1") is None


def test_cache_invalidate_prefix():
    cache = TTLCache(default_ttl=60)
    cache.set("analytics:kpis:co1", "v1")
    cache.set("analytics:revenue:co1", "v2")
    cache.set("payout:summary:co1", "v3")
    cache.invalidate_prefix("analytics:")
    assert cache.get("analytics:kpis:co1") is None
    assert cache.get("analytics:revenue:co1") is None
    assert cache.get("payout:summary:co1") == "v3"


def test_cache_clear():
    cache = TTLCache(default_ttl=60)
    cache.set("a", 1)
    cache.set("b", 2)
    cache.clear()
    assert cache.get("a") is None
    assert cache.get("b") is None


def test_make_cache_key():
    key = make_cache_key("analytics:kpis", company="techo", role="exec", period="2025-Q1")
    assert "analytics:kpis" in key
    assert "techo" in key
    assert "exec" in key
    assert "period=2025-Q1" in key


def test_cache_max_size():
    cache = TTLCache(default_ttl=60, max_size=3)
    cache.set("a", 1)
    cache.set("b", 2)
    cache.set("c", 3)
    cache.set("d", 4)  # Should evict oldest
    # At most 3 items should remain
    count = sum(1 for k in ["a", "b", "c", "d"] if cache.get(k) is not None)
    assert count <= 3
