from __future__ import annotations

import asyncio

import pytest

import backend.company_context as company_context
from backend import data_generator
from backend.company_context import (
    ensure_company_loaded,
    get_active_company,
    load_company_into_context,
    set_active_company,
    wait_for_company_load_completion,
)


@pytest.mark.asyncio
async def test_ensure_company_loaded_serializes_concurrent_calls(monkeypatch) -> None:
    original = get_active_company()
    calls = 0

    async def fake_load_company_dataset(company_name: str):
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.02)
        return {"company": company_name, "rows": 1}

    monkeypatch.setattr(data_generator, "load_company_dataset", fake_load_company_dataset)

    try:
        set_active_company(None)
        results = await asyncio.gather(*[ensure_company_loaded("insurex") for _ in range(5)])

        assert calls == 1
        assert get_active_company() == "insurex"
        assert sum(1 for item in results if item is True) == 1
    finally:
        set_active_company(original)


@pytest.mark.asyncio
async def test_load_company_into_context_skips_when_already_active(monkeypatch) -> None:
    original = get_active_company()
    calls = 0

    async def fake_load_company_dataset(_company_name: str):
        nonlocal calls
        calls += 1
        return {"rows": 1}

    monkeypatch.setattr(data_generator, "load_company_dataset", fake_load_company_dataset)

    try:
        set_active_company("insurex")
        counts = await load_company_into_context("insurex")

        assert counts == {}
        assert calls == 0
    finally:
        set_active_company(original)


@pytest.mark.asyncio
async def test_load_company_into_context_force_reload(monkeypatch) -> None:
    original = get_active_company()
    calls = 0

    async def fake_load_company_dataset(_company_name: str):
        nonlocal calls
        calls += 1
        return {"rows": 2}

    monkeypatch.setattr(data_generator, "load_company_dataset", fake_load_company_dataset)

    try:
        set_active_company("insurex")
        counts = await load_company_into_context("insurex", force_reload=True)

        assert counts == {"rows": 2}
        assert calls == 1
    finally:
        set_active_company(original)


@pytest.mark.asyncio
async def test_wait_for_company_load_completion_waits_for_active_reload(monkeypatch) -> None:
    original = get_active_company()
    monkeypatch.setattr(company_context, "_context_lock", asyncio.Lock())
    monkeypatch.setattr(company_context, "_company_load_in_progress", False)

    async def fake_load_company_dataset(_company_name: str):
        await asyncio.sleep(0.05)
        return {"rows": 1}

    monkeypatch.setattr(data_generator, "load_company_dataset", fake_load_company_dataset)

    try:
        set_active_company(None)
        loader_task = asyncio.create_task(load_company_into_context("insurex", force_reload=True))
        # Give loader task a chance to acquire the lock and enter sleep.
        await asyncio.sleep(0.01)

        started = asyncio.get_running_loop().time()
        await wait_for_company_load_completion()
        elapsed = asyncio.get_running_loop().time() - started

        await loader_task

        assert elapsed >= 0.02
        assert get_active_company() == "insurex"
    finally:
        set_active_company(original)
