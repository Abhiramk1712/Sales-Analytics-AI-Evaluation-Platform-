"""
Shared test fixtures.

Most of this suite needs no database — it reads CSVs, exercises pure functions, or
builds a FastAPI app with dependencies overridden. The fixtures here are for the
tests that genuinely do, and they are **opt-in**: a test asks for `db_schema` (or
for `fresh_engine`, which depends on it). Making them autouse would force every
test in the suite to require a running PostgreSQL, which is not true today and
would make the suite harder to run, not easier.
"""
from __future__ import annotations

import pytest


@pytest.fixture(scope="session")
def db_schema():
    """
    Ensure the tables exist before a database test runs.

    CI starts an empty PostgreSQL service and runs `pytest -q` directly, so
    nothing creates the schema first — which is how the tenancy tests passed
    locally (against a database built by earlier runs) and failed in CI with
    `relation "teams" does not exist`.

    `create_all` is used rather than `alembic upgrade head` because that is how
    this application actually bootstraps: `AUTO_CREATE_TABLES` defaults to true,
    and the baseline migration is an explicit no-op that documents exactly this
    ("current environments bootstrap via SQLAlchemy metadata"). Running the
    migrations here would create nothing and hide the difference.
    """
    import asyncio

    from backend.database import Base, get_engine

    async def _create() -> None:
        engine = get_engine()
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        await engine.dispose()

    import backend.models  # noqa: F401  — registers the tables on Base.metadata

    asyncio.run(_create())

    # The engine used above belongs to a now-closed loop; drop it so tests build
    # their own in the loop they actually run under.
    import backend.database as database

    database._engine = None
    database._async_session_factory = None

    yield
