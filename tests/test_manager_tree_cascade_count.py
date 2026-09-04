"""
tests/test_manager_tree_cascade_count.py
==========================================
GET /analytics/manager-tree hardcoded "cascade_rule_count": 0 on every node
("populated below if needed" — nothing below ever did). The frontend
(OrgHierarchyPage.jsx) renders a "N cascade" badge on any node with
cascade_rule_count > 0, so that badge never once rendered, even for a
rank-1 executive who genuinely owns a real, active PlanCascadeRule.

"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import delete

from backend.database import get_session_factory
from backend.models import UserProfile, Manager, Plan, PlanCascadeRule
from backend.routers.analytics import manager_tree
from backend.tenancy import tenant_scope
from backend.tenant_guard import unscoped

COMPANY = f"test-manager-tree-{uuid.uuid4().hex[:8]}"


@pytest.fixture(autouse=True)
async def fresh_engine(db_schema):
    import backend.database as database

    database._engine = None
    database._async_session_factory = None
    yield
    engine = database._engine
    if engine is not None:
        await engine.dispose()
    database._engine = None
    database._async_session_factory = None


@pytest.fixture
async def cleanup():
    yield
    factory = get_session_factory()
    async with factory() as db, unscoped():
        await db.execute(delete(PlanCascadeRule).where(PlanCascadeRule.company_id == COMPANY))
        await db.execute(delete(Plan).where(Plan.company_id == COMPANY))
        await db.execute(delete(Manager).where(Manager.company_id == COMPANY))
        await db.execute(delete(UserProfile).where(UserProfile.company_id == COMPANY))
        await db.commit()


@pytest.mark.asyncio
async def test_cascade_rule_count_reflects_real_rules_owned_by_the_node(cleanup):
    factory = get_session_factory()
    async with factory() as db, tenant_scope(COMPANY):
        exec_user = UserProfile(name="Exec", email="exec@example.com")
        ic_user = UserProfile(name="IC", email="ic@example.com")
        db.add_all([exec_user, ic_user])
        await db.flush()

        db.add(Manager(user_id=exec_user.id, manager_user_id=None))
        db.add(Manager(user_id=ic_user.id, manager_user_id=exec_user.id))

        plan = Plan(name="Global Plan", scope="global", owner_user_id=exec_user.id)
        db.add(plan)
        await db.flush()
        db.add(PlanCascadeRule(plan_id=plan.id, owner_user_id=exec_user.id, cascade_scope="all_reports"))

        await db.commit()

        result = await manager_tree(db=db)

    root = result["nodes"][0]
    assert root["id"] == str(exec_user.id)
    assert root["cascade_rule_count"] == 1
    assert root["reports"][0]["cascade_rule_count"] == 0
