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
from backend.payout.audit_trail_service import adjust_payout, approve_payout, get_payout, list_payouts, lock_payout
from backend.validation.quality_gate import get_critical_issues

router = APIRouter(
    prefix="/payouts",
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
) -> dict[str, Any]:
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
