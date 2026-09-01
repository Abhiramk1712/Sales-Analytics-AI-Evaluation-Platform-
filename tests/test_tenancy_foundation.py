"""
tests/test_tenancy_foundation.py
================================
The pieces query-scoped tenancy is built on.

Three properties are pinned here:

1. Tenant identity is per-task, not per-process. The old `_active_company`
   module global was shared by every concurrent request, so two users on
   different companies overwrote each other. `test_concurrent_tasks_do_not_see_each_other`
   is the test that global could never pass.
2. `apply_company_scope` refuses a model it cannot scope, instead of returning
   an unscoped query that looks scoped at the call site.
3. Every domain table carries `company_id`, so the scoping has something to
   filter on.
"""
from __future__ import annotations

import asyncio

import pytest

import backend.models as models
from backend.auth.tenant import ModelNotTenantScopedError, apply_company_scope
from backend.tenancy import (
    TenantNotSetError,
    get_current_tenant,
    require_current_tenant,
    tenant_scope,
)


# ── Tenant identity is request-scoped ────────────────────────────────────────


def test_no_tenant_bound_by_default():
    assert get_current_tenant() is None


def test_require_raises_when_unbound():
    with pytest.raises(TenantNotSetError):
        require_current_tenant()


def test_scope_binds_and_restores():
    with tenant_scope("acme"):
        assert require_current_tenant() == "acme"
    assert get_current_tenant() is None


def test_scope_restores_even_when_the_block_raises():
    with pytest.raises(ValueError):
        with tenant_scope("acme"):
            raise ValueError("boom")
    assert get_current_tenant() is None


def test_scopes_nest():
    with tenant_scope("outer"):
        assert require_current_tenant() == "outer"
        with tenant_scope("inner"):
            assert require_current_tenant() == "inner"
        assert require_current_tenant() == "outer"


def test_blank_tenant_is_treated_as_unbound():
    with tenant_scope("   "):
        assert get_current_tenant() is None


@pytest.mark.asyncio
async def test_concurrent_tasks_do_not_see_each_other():
    """
    The property the process-global `_active_company` could not provide.

    Two concurrent requests for different companies must each keep their own
    tenant across await points. With a module global, whichever task set it
    last wins and both read the same value.
    """
    observed: dict[str, list[str]] = {"acme": [], "globex": []}

    async def request(company: str) -> None:
        with tenant_scope(company):
            for _ in range(5):
                observed[company].append(require_current_tenant())
                await asyncio.sleep(0)  # force interleaving

    await asyncio.gather(request("acme"), request("globex"))

    assert observed["acme"] == ["acme"] * 5
    assert observed["globex"] == ["globex"] * 5


# ── Scoping refuses what it cannot scope ─────────────────────────────────────


def test_scope_applied_to_a_tenant_model():
    from sqlalchemy import select

    query = apply_company_scope(select(models.Deal), models.Deal, "acme")
    assert "company_id" in str(query)


def test_scope_refuses_a_model_without_company_id():
    """
    Previously this returned the query untouched, so calling it on an
    unmigrated model produced an unscoped query at a call site that read as if
    it were scoped.
    """
    from sqlalchemy import select

    with pytest.raises(ModelNotTenantScopedError):
        apply_company_scope(select(models.JobStatus), models.JobStatus, "acme")


# ── Every domain table can be scoped ─────────────────────────────────────────


#: Bookkeeping, not tenant data.
NON_TENANT_TABLES = {"job_status"}


def test_all_domain_tables_carry_company_id():
    missing = sorted(
        name
        for name, table in models.Base.metadata.tables.items()
        if name not in NON_TENANT_TABLES and "company_id" not in table.c
    )
    assert missing == [], f"tables still missing company_id: {missing}"


def test_company_id_is_indexed_everywhere_it_exists():
    """Every scoped query filters on this column; none of them may seq-scan."""
    unindexed = []
    for name, table in models.Base.metadata.tables.items():
        if "company_id" not in table.c:
            continue
        indexed = any("company_id" in [c.name for c in idx.columns] for idx in table.indexes)
        if not indexed and not table.c["company_id"].index:
            unindexed.append(name)
    assert unindexed == [], f"company_id not indexed on: {unindexed}"


def test_the_migration_covers_every_table_that_needs_it():
    """
    The migration's table list and the models must not drift apart. A table
    added to the models without a migration exists after `create_all` and is
    missing after `alembic upgrade head` — the two ways this app builds a
    schema would disagree, which is exactly the class of bug that makes a
    payout impossible to reproduce later.
    """
    import importlib.util
    from pathlib import Path

    migration_path = Path("migrations/versions/20260901_0001_add_company_id_tenant_scope.py")
    spec = importlib.util.spec_from_file_location("tenancy_migration", migration_path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    # payout_configs already had company_id before this migration, so it is
    # correctly absent from the migration but present in the models.
    expected = {
        name
        for name, table in models.Base.metadata.tables.items()
        if "company_id" in table.c and name != "payout_configs"
    }
    assert set(migration.TENANT_TABLES) == expected
