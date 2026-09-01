"""
tests/test_tenancy_enforcement.py
=================================
Query-scoped tenancy, end to end against a real database.

The headline is `test_concurrent_tenants_cannot_see_each_other`. Under the
previous design that test could not be made to pass: tenant separation was a
process-global plus a whole-database reload, so two concurrent requests for
different companies either saw the same rows or destroyed each other's data
mid-query. It is the test that proves ARCH-1 actually landed rather than being
described.

These tests write and read real rows, each under a tenant of its own, and clean
up after themselves.
"""
from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy import delete, func, select

from backend.database import get_session_factory
from backend.models import Rep, Team
from backend.tenancy import get_current_tenant, tenant_scope
from backend.tenant_guard import TenantStampError, unscoped

ALPHA = f"test-alpha-{uuid.uuid4().hex[:8]}"
BETA = f"test-beta-{uuid.uuid4().hex[:8]}"


@pytest.fixture(autouse=True)
async def fresh_engine(db_schema):
    """
    Give each test its own engine.

    The async engine caches connections against the event loop that created them,
    and pytest-asyncio runs each test in a new loop — so a pooled connection from
    an earlier test surfaces as asyncpg's "another operation is in progress".
    Resetting the module-level engine forces connections to be made in the loop
    that will actually use them.
    """
    import backend.database as database

    database._engine = None
    database._async_session_factory = None
    yield
    engine = database._engine
    if engine is not None:
        await engine.dispose()
    database._engine = None
    database._async_session_factory = None


async def _make_team(company: str, name: str) -> uuid.UUID:
    """Insert one team under `company` and return its id."""
    factory = get_session_factory()
    team_id = uuid.uuid4()
    async with factory() as db, tenant_scope(company):
        db.add(Team(id=team_id, name=name, region="test"))
        await db.commit()
    return team_id


async def _count_teams(company: str) -> int:
    factory = get_session_factory()
    async with factory() as db, tenant_scope(company):
        return (await db.execute(select(func.count(Team.id)))).scalar() or 0


@pytest.fixture
async def two_tenants():
    """Two companies with distinct data, removed afterwards."""
    await _make_team(ALPHA, "alpha-team-1")
    await _make_team(ALPHA, "alpha-team-2")
    await _make_team(BETA, "beta-team-1")
    yield
    factory = get_session_factory()
    async with factory() as db, unscoped():
        for company in (ALPHA, BETA):
            await db.execute(delete(Rep).where(Rep.company_id == company))
            await db.execute(delete(Team).where(Team.company_id == company))
        await db.commit()


# ── Isolation ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_each_tenant_sees_only_its_own_rows(two_tenants):
    assert await _count_teams(ALPHA) == 2
    assert await _count_teams(BETA) == 1


@pytest.mark.asyncio
async def test_a_tenant_with_no_data_sees_nothing(two_tenants):
    assert await _count_teams(f"test-empty-{uuid.uuid4().hex[:8]}") == 0


@pytest.mark.asyncio
async def test_concurrent_tenants_cannot_see_each_other(two_tenants):
    """
    The property the process-global `_active_company` could not provide.

    Two tasks interleave reads for different companies across await points. With
    a shared global, whichever task set it last determined what *both* saw; with
    the reload-on-mismatch middleware, one task's query ran against a database
    the other was in the middle of rebuilding.
    """
    seen: dict[str, list[int]] = {ALPHA: [], BETA: []}

    async def reader(company: str) -> None:
        for _ in range(6):
            seen[company].append(await _count_teams(company))
            await asyncio.sleep(0)  # force interleaving

    await asyncio.gather(reader(ALPHA), reader(BETA))

    assert seen[ALPHA] == [2] * 6, seen
    assert seen[BETA] == [1] * 6, seen


@pytest.mark.asyncio
async def test_loading_one_tenant_leaves_the_other_intact(two_tenants):
    """
    Replacing one company's rows must not touch another's — the whole point of
    replacing `drop_all` with a scoped delete.
    """
    from backend.data_generator import _delete_company_rows

    await _delete_company_rows(ALPHA)

    assert await _count_teams(ALPHA) == 0
    assert await _count_teams(BETA) == 1, "BETA was collateral damage"


# ── Stamping ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_inserts_are_stamped_with_the_bound_tenant(two_tenants):
    factory = get_session_factory()
    async with factory() as db, tenant_scope(ALPHA):
        team = Team(id=uuid.uuid4(), name="stamped", region="test")
        db.add(team)
        await db.commit()
        assert team.company_id == ALPHA


@pytest.mark.asyncio
async def test_writing_with_no_tenant_bound_is_refused():
    """
    Silently writing an unscoped row is how data becomes invisible to every
    tenant later. Fail at the write instead.
    """
    factory = get_session_factory()
    with pytest.raises(TenantStampError, match="no company_id"):
        async with factory() as db:
            db.add(Team(id=uuid.uuid4(), name="orphan", region="test"))
            await db.commit()


@pytest.mark.asyncio
async def test_an_explicit_company_id_is_respected(two_tenants):
    factory = get_session_factory()
    async with factory() as db, tenant_scope(ALPHA):
        team = Team(id=uuid.uuid4(), name="explicit", region="test", company_id=BETA)
        db.add(team)
        await db.commit()
    assert await _count_teams(BETA) == 2


# ── The escape hatch is explicit ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_unscoped_sees_every_tenant(two_tenants):
    factory = get_session_factory()
    async with factory() as db, tenant_scope(ALPHA), unscoped():
        total = (await db.execute(
            select(func.count(Team.id)).where(Team.company_id.in_([ALPHA, BETA]))
        )).scalar()
    assert total == 3


@pytest.mark.asyncio
async def test_scope_is_restored_after_unscoped(two_tenants):
    with tenant_scope(ALPHA):
        with unscoped():
            pass
        assert get_current_tenant() == ALPHA
    assert await _count_teams(ALPHA) == 2


# ── The filter needs an entity to attach to ──────────────────────────────────


def test_no_bare_entity_less_counts_in_scoped_queries():
    """
    `select(func.count())` silently escapes tenant filtering.

    `with_loader_criteria` attaches to mapped entities in a statement's columns
    clause. A bare count has none — the entity appears only in the WHERE — so the
    filter is never applied and the query counts every tenant's rows.

    This was not hypothetical: `/analytics/kpis` reported `open_deal_count: 205`
    for both companies, which is exactly techo-solutions' 101 plus insurex's 104.
    Every other figure on the same response was correctly scoped, so nothing
    looked wrong until two tenants were resident at once and the numbers were
    compared.

    Write `select(func.count(Model.id))` instead. Genuinely entity-less counts —
    over a subquery, or an outer-join orphan check where a company predicate
    would change the meaning — are listed here deliberately.
    """
    import re
    from pathlib import Path

    allowed = {
        # Counts rows of a subquery, which carries no mapped entity. The
        # subquery it wraps is itself scoped.
        "backend/routers/analytics.py",
        # Cross-entity checks: several are outer-join orphan probes where adding
        # a company predicate to the outer side changes what "orphan" means.
        # Tenant-scoping the data-quality surface is tracked separately.
        "backend/routers/data_quality.py",
        "backend/agent/tools/pipeline_tools.py",
    }

    backend = Path(__file__).resolve().parent.parent / "backend"
    offenders: list[str] = []
    for path in backend.rglob("*.py"):
        rel = path.relative_to(backend.parent).as_posix()
        if rel in allowed:
            continue
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(r"select\(\s*func\.count\(\)\s*\)", line):
                offenders.append(f"{rel}:{lineno}")

    assert not offenders, (
        "entity-less count() bypasses tenant filtering; use func.count(Model.id): "
        + ", ".join(offenders)
    )


# ── Every route is scoped unless explicitly exempt ───────────────────────────


def test_every_mounted_route_is_tenant_scoped_or_explicitly_exempt():
    """
    The middleware exempts paths rather than allowlisting them, so a router
    added later is scoped by default.

    The previous allowlist named seven prefixes and omitted /agent, /workflows,
    /etl and /grading. Those four served tenant data with no tenant bound, and
    the agent reported $10.36M revenue for a company whose revenue is $3.76M —
    the sum of every company in the database.
    """
    from backend.main import app, is_tenant_exempt

    unscoped_paths = [
        route.path
        for route in app.routes
        if getattr(route, "path", None) and is_tenant_exempt(route.path)
    ]

    # Only the handful of genuinely tenant-free paths may be exempt.
    allowed_exempt = {"/", "/health", "/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect"}
    unexpected = sorted(set(unscoped_paths) - allowed_exempt)
    assert unexpected == [], f"these routes are exempt from tenant binding: {unexpected}"


def test_data_serving_prefixes_are_all_scoped():
    """Each router that returns tenant data must be bound by the middleware."""
    from backend.main import is_tenant_exempt

    for prefix in (
        "/analytics", "/payout", "/payouts", "/ml", "/reports", "/data-quality",
        "/plans", "/territories", "/agent", "/workflows", "/etl", "/grading",
        "/ingestion",
    ):
        assert not is_tenant_exempt(f"{prefix}/anything"), f"{prefix} is not tenant-scoped"
