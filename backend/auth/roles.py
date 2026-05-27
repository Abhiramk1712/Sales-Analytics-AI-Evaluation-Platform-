"""
backend/auth/roles.py
=====================
Role definitions and permission sets for the Sales Analytics AI platform.

Demo mode: roles are passed via HTTP headers (X-User-Role).
Production: replace with JWT/OAuth middleware.
"""
from __future__ import annotations
from typing import Set

from backend.auth.permissions import ROLE_PERMISSIONS

# ── Role constants ─────────────────────────────────────────────────────────
ROLE_EXECUTIVE    = "executive"
ROLE_REVOPS_ADMIN = "revops_admin"
ROLE_FINANCE_ADMIN = "finance_admin"
ROLE_SALES_MANAGER = "sales_manager"
ROLE_SALES_REP = "sales_rep"
ROLE_DATA_SCIENTIST = "data_scientist"
ROLE_AUDITOR = "auditor"

ALL_ROLES: Set[str] = {
    ROLE_EXECUTIVE,
    ROLE_REVOPS_ADMIN,
    ROLE_FINANCE_ADMIN,
    ROLE_SALES_MANAGER,
    ROLE_SALES_REP,
    ROLE_DATA_SCIENTIST,
    ROLE_AUDITOR,
}

# ── Position rank → role mapping (for auto-detection) ─────────────────────
RANK_TO_ROLE: dict[int, str] = {
    1: ROLE_EXECUTIVE,
    2: ROLE_EXECUTIVE,
    3: ROLE_SALES_MANAGER,
    4: ROLE_SALES_MANAGER,
    5: ROLE_SALES_REP,
}

def permissions_for_role(role: str) -> Set[str]:
    return set(ROLE_PERMISSIONS.get(role, set()))


def has_permission(role: str, permission: str) -> bool:
    """Return True if the given role has the specified permission."""
    return permission in ROLE_PERMISSIONS.get(role, set())


def role_display_name(role: str) -> str:
    _names = {
        ROLE_EXECUTIVE:    "Executive",
        ROLE_REVOPS_ADMIN: "RevOps Admin",
        ROLE_FINANCE_ADMIN: "Finance Admin",
        ROLE_SALES_MANAGER: "Sales Manager",
        ROLE_SALES_REP: "Sales Rep",
        ROLE_DATA_SCIENTIST: "Data Scientist",
        ROLE_AUDITOR: "Auditor",
    }
    return _names.get(role, role.title())
