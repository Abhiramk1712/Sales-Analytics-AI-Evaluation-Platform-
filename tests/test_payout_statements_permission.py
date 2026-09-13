"""
tests/test_payout_statements_permission.py
=============================================
GET /payout/statements/{rep_id} is a single-rep, read-only "show me my own
statement" endpoint -- exactly what PERM_VIEW_OWN_PAYOUT exists for
(sales_rep and sales_manager both hold it). But it lived on the same
router as /payout/team-summary, /payout/config, /payout/quota-fairness,
rule management, etc., all gated by a single router-level
require_permission("view_payouts") dependency -- the broader "view
everyone's payouts" permission, which sales_rep does not have.

Confirmed live: a sales_rep session got 403 "Role 'sales_rep' does not
have permission 'view_payouts'" trying to view their OWN payout_id's
statement -- discovered while building the Sales Rep home page, which
needs this endpoint for the quota-streak calculation.

Fixed by moving this one route to backend/routers/payout.statements_router,
a second router sharing the /payout prefix with only
Depends(get_tenant_context) at the router level, and adding an explicit
Depends(require_any_permission("view_payouts", "view_own_payout")) to the
route itself -- a router-level dependency runs unconditionally for every
route on that router with no per-route override, so admitting
view_own_payout here without touching the other 15 payout.py endpoints
needed a second router, not a change to the shared one.

The HTTP-level tests below use a fake `db` (mirrors
test_enterprise_endpoints.py's own async def fake_db(): yield object()
pattern) rather than a real database -- a plain `TestClient` call and a
pytest-asyncio-managed real asyncpg session don't share an event loop
(confirmed: mixing them here raised "got Future attached to a different
loop"), which is exactly why every other TestClient test in this repo
either fully mocks `db` or skips TestClient and calls the router function
directly. A rep_id that resolves to "not found" is precise enough to prove
the permission layer let the request through -- the actual statement
math/data shape is already covered by
tests/test_payout_statements_quarterly_grain.py.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from backend.auth.dependencies import require_any_permission
from backend.auth.models import UserContext
from backend.routers import payout as payout_router


# ── require_any_permission itself ───────────────────────────────────────────

def _ctx(role: str) -> UserContext:
    return UserContext(
        user_id="u-1", role=role, team_id=None, territory_id=None, company_id=None,
        permissions=set(), auth_source="demo", is_demo=True,
    )


def test_require_any_permission_passes_when_either_is_held():
    check = require_any_permission("view_payouts", "view_own_payout")
    # sales_rep holds view_own_payout but not view_payouts.
    assert check(ctx=_ctx("sales_rep")).role == "sales_rep"
    # revops_admin holds view_payouts.
    assert check(ctx=_ctx("revops_admin")).role == "revops_admin"


def test_require_any_permission_rejects_when_neither_is_held():
    check = require_any_permission("view_payouts", "view_own_payout")
    with pytest.raises(HTTPException) as exc_info:
        check(ctx=_ctx("not-a-real-role"))
    assert exc_info.value.status_code == 403


# ── The actual endpoint, over real HTTP, through FastAPI's dependency graph ──

class _FakeScalarResult:
    def scalars(self):
        return self

    def first(self):
        return None


class _FakeDB:
    """No rep ever matches -- the route's own "Rep not found" 404 is the
    signal that a request got PAST the permission layer (a 403 from that
    layer never reaches this fake at all)."""

    async def execute(self, *_args, **_kwargs):
        return _FakeScalarResult()


def _build_app():
    app = FastAPI()
    app.include_router(payout_router.router)
    app.include_router(payout_router.statements_router)

    async def fake_db():
        yield _FakeDB()

    app.dependency_overrides[payout_router.get_db] = fake_db
    return app


def test_sales_rep_can_reach_their_own_statement_but_not_team_summary():
    """The regression this file exists for: sales_rep must get PAST the
    permission layer on its own statement (reaching the route's own 404
    for an unknown rep, not a 403) while staying blocked from the broader
    payout.router endpoints (403) -- proving the fix is scoped to the one
    route, not a loosening of the whole /payout surface."""
    client = TestClient(_build_app())
    headers = {"X-User-Role": "sales_rep", "X-Company-Id": "techo-solutions"}
    fake_rep_id = str(uuid.uuid4())

    statement_res = client.get(f"/payout/statements/{fake_rep_id}?periods=3", headers=headers)
    assert statement_res.status_code == 404, statement_res.json()
    assert "not found" in statement_res.json()["detail"].lower()

    team_summary_res = client.get("/payout/team-summary?period=2026-Q2", headers=headers)
    assert team_summary_res.status_code == 403


def test_sales_manager_and_revops_admin_still_reach_their_own_statement():
    """The two roles that already held view_payouts must be unaffected."""
    client = TestClient(_build_app())
    fake_rep_id = str(uuid.uuid4())

    for role in ("sales_manager", "revops_admin"):
        res = client.get(
            f"/payout/statements/{fake_rep_id}?periods=3",
            headers={"X-User-Role": role, "X-Company-Id": "techo-solutions"},
        )
        assert res.status_code == 404, f"{role}: {res.status_code} {res.json()}"
