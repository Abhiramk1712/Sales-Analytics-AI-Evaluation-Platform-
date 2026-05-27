"""
backend/utils/identity_mapping.py
================================
Canonical helpers for mapping between Rep and UserProfile records.

The current schema links reps and users by matching email values.
These helpers centralize that logic so routers/services do not duplicate it.
"""
from __future__ import annotations

import uuid
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import Rep, UserProfile


async def get_user_for_rep(db: AsyncSession, rep_id: uuid.UUID) -> UserProfile | None:
    """Return the UserProfile mapped to a rep by email."""
    rep = (await db.execute(select(Rep).where(Rep.id == rep_id))).scalars().first()
    if not rep or not rep.email:
        return None
    return (
        await db.execute(
            select(UserProfile).where(UserProfile.email == rep.email)
        )
    ).scalars().first()


async def get_rep_for_user(db: AsyncSession, user_id: uuid.UUID) -> Rep | None:
    """Return the Rep mapped to a user by email."""
    user = (await db.execute(select(UserProfile).where(UserProfile.id == user_id))).scalars().first()
    if not user or not user.email:
        return None
    return (
        await db.execute(
            select(Rep).where(Rep.email == user.email)
        )
    ).scalars().first()


async def get_rep_ids_for_user_ids(
    db: AsyncSession,
    user_ids: Iterable[uuid.UUID],
) -> list[uuid.UUID]:
    """Bulk map user IDs to rep IDs using email joins in memory."""
    user_ids_list = list(user_ids)
    if not user_ids_list:
        return []

    users = (
        await db.execute(select(UserProfile).where(UserProfile.id.in_(user_ids_list)))
    ).scalars().all()
    emails = [(u.email or "").strip().lower() for u in users if u.email]
    if not emails:
        return []

    reps = (
        await db.execute(select(Rep).where(Rep.email.in_(emails)))
    ).scalars().all()
    return [r.id for r in reps]
