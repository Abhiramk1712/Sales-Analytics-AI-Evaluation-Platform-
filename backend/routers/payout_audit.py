"""Payout audit lifecycle endpoints (enterprise scaffold)."""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth.dependencies import get_user_context, require_permission
from backend.auth.models import UserContext
from backend.auth.tenant import get_current_company_id, get_tenant_context
from backend.database import get_db
from backend.payout.audit_trail_service import (
    adjust_payout,
    approve_payout,
    get_payout,
    list_payouts,
    lock_payout,
    mark_paid,
    mark_reviewed,
    seed_from_db_record,
)
from backend.validation.quality_gate import get_critical_issues

router = APIRouter(
    # README's own API Surface table already documented this as /payout-audit
    # (see README.md's API Surface section) — the code just never matched. One
    # character apart from backend/routers/payout.py's /payout prefix (a
    # different router entirely: calculation/statements/config, not lifecycle),
    # easy to misread when skimming route tables.
    prefix="/payout-audit",
    tags=["Payout Audit"],
    dependencies=[Depends(require_permission("view_payouts")), Depends(get_tenant_context)],
)


class ApprovePayoutRequest(BaseModel):
    note: Optional[str] = None


class AdjustPayoutRequest(BaseModel):
    adjustment_amount: float = Field(..., description="Signed payout adjustment amount")
    reason: str = Field(..., min_length=3)


@router.get("")
async def list_payout_records(
    lifecycle_state: Optional[str] = Query(None),
    company_id: str = Depends(get_current_company_id),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    # Register every real PayoutRecord as an actionable in-memory audit
    # record (idempotent -- a record that already has lifecycle/approval
    # state, from a prior action or a real /payout/calculate or
    # /payout/team-summary run, is left untouched). Previously this only ran
    # when the in-memory store was *completely* empty for the company, which
    # meant two live bugs: (1) a payout nothing had explicitly computed was
    # shown with working-looking action buttons that 404'd on click, because
    # the display-only fallback row was never actually registered; (2) once
    # a single real record existed for the company (e.g. from visiting the
    # Payouts tab for one quarter), every *other* period's PayoutRecord
    # stopped being surfaced at all, since the store was no longer empty.
    from sqlalchemy import select as _sel
    from backend.models import PayoutRecord, UserProfile, Rep

    payout_records = (await db.execute(
        _sel(PayoutRecord).order_by(PayoutRecord.created_at.desc())
    )).scalars().all()

    if payout_records:
        rep_rows = (await db.execute(_sel(Rep.id, Rep.name, Rep.email))).all()
        rep_name_by_email: dict[str, str] = {str(r.email).lower(): r.name for r in rep_rows if r.email}
        rep_id_by_email: dict[str, str] = {str(r.email).lower(): str(r.id) for r in rep_rows if r.email}
        user_rows = (await db.execute(_sel(UserProfile.id, UserProfile.email, UserProfile.name))).all()
        user_id_to_email: dict[str, str] = {str(u.id): str(u.email or "").lower() for u in user_rows}

        for pr in payout_records:
            email = user_id_to_email.get(str(pr.user_id), "")
            seed_from_db_record(
                payout_id=str(pr.id),
                company_id=company_id,
                period=pr.period,
                user_id=str(pr.user_id) if pr.user_id else None,
                rep_id=rep_id_by_email.get(email),
                rep_name=rep_name_by_email.get(email, "Unknown"),
                plan_id=str(pr.plan_id) if pr.plan_id else None,
                credited_amount=float(pr.payout_amount or 0),
                final_payout=float(pr.payout_amount or 0),
                confidence=float(pr.confidence or 0),
                fallback_used=bool(pr.fallback_used),
            )

    rows = list_payouts(company_id=company_id)

    if lifecycle_state:
        rows = [r for r in rows if str(r.get("lifecycle_state", "")).lower() == lifecycle_state.lower()]

    return {
        "company_id": company_id,
        "count": len(rows),
        "rows": rows,
    }


@router.get("/{payout_id}")
async def get_payout_record(
    payout_id: str,
    company_id: str = Depends(get_current_company_id),
) -> dict[str, Any]:
    row = get_payout(payout_id)
    if not row:
        raise HTTPException(status_code=404, detail="Payout record not found")
    if row.get("company_id") != company_id:
        raise HTTPException(status_code=404, detail="Payout record not found for active company")
    return row


@router.get("/{payout_id}/trace")
async def get_payout_trace(
    payout_id: str,
    company_id: str = Depends(get_current_company_id),
) -> dict[str, Any]:
    row = get_payout(payout_id)
    if not row:
        raise HTTPException(status_code=404, detail="Payout record not found")
    if row.get("company_id") != company_id:
        raise HTTPException(status_code=404, detail="Payout record not found for active company")

    return {
        "payout_id": payout_id,
        "company_id": company_id,
        "lifecycle_state": row.get("lifecycle_state"),
        "calculation_trace_json": row.get("calculation_trace_json", {}),
        "source_records_json": row.get("source_records_json", {}),
    }


@router.post("/{payout_id}/review")
async def review_payout_record(
    payout_id: str,
    ctx: UserContext = Depends(get_user_context),
    _: Any = Depends(require_permission("approve_payouts")),
) -> dict[str, Any]:
    try:
        return mark_reviewed(payout_id, actor=ctx.user_id or "revops-admin")
    except KeyError:
        raise HTTPException(status_code=404, detail="Payout record not found")
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@router.post("/{payout_id}/approve")
async def approve_payout_record(
    payout_id: str,
    payload: ApprovePayoutRequest,
    db: AsyncSession = Depends(get_db),
    ctx: UserContext = Depends(get_user_context),
    _: Any = Depends(require_permission("approve_payouts")),
) -> dict[str, Any]:
    critical = await get_critical_issues(db)
    if critical:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "Payout approval blocked by critical data quality issues.",
                "critical_issues": critical,
            },
        )

    try:
        record = approve_payout(payout_id, actor=ctx.user_id or "finance-admin")
        if payload.note:
            trace = dict(record.get("calculation_trace_json") or {})
            trace["approval_note"] = payload.note
            record["calculation_trace_json"] = trace
        return record
    except KeyError:
        raise HTTPException(status_code=404, detail="Payout record not found")
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@router.post("/{payout_id}/lock")
async def lock_payout_record(
    payout_id: str,
    ctx: UserContext = Depends(get_user_context),
    _: Any = Depends(require_permission("approve_payouts")),
) -> dict[str, Any]:
    try:
        return lock_payout(payout_id, actor=ctx.user_id or "finance-admin")
    except KeyError:
        raise HTTPException(status_code=404, detail="Payout record not found")
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@router.post("/{payout_id}/pay")
async def mark_payout_paid(
    payout_id: str,
    ctx: UserContext = Depends(get_user_context),
    _: Any = Depends(require_permission("approve_payouts")),
) -> dict[str, Any]:
    try:
        return mark_paid(payout_id, actor=ctx.user_id or "finance-admin")
    except KeyError:
        raise HTTPException(status_code=404, detail="Payout record not found")
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@router.post("/{payout_id}/adjust")
async def adjust_payout_record(
    payout_id: str,
    payload: AdjustPayoutRequest,
    ctx: UserContext = Depends(get_user_context),
    _: Any = Depends(require_permission("approve_payouts")),
) -> dict[str, Any]:
    try:
        return adjust_payout(
            payout_id,
            actor=ctx.user_id or "finance-admin",
            adjustment_amount=payload.adjustment_amount,
            reason=payload.reason,
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="Payout record not found")
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
