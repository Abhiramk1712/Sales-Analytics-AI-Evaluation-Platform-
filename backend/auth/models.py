"""Typed auth models used by RBAC dependencies and route guards."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from fastapi import HTTPException

from backend.auth.roles import has_permission, permissions_for_role


@dataclass
class UserContext:
    user_id: Optional[str] = None
    role: str = "executive"
    team_id: Optional[str] = None
    territory_id: Optional[str] = None
    company_id: Optional[str] = None
    permissions: set[str] = field(default_factory=set)
    auth_source: str = "demo"
    is_demo: bool = True

    def __post_init__(self) -> None:
        # Keep backward compatibility for tests/callers that only pass role.
        if not self.permissions:
            self.permissions = permissions_for_role(self.role)

    @property
    def is_admin(self) -> bool:
        return self.can("admin")

    def can(self, permission: str) -> bool:
        return permission in self.permissions or has_permission(self.role, permission)

    def require_permission(self, permission: str) -> None:
        if not self.can(permission):
            raise HTTPException(status_code=403, detail=f"Missing permission: {permission}")
