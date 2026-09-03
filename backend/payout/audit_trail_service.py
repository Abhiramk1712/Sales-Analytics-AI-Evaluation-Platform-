"""In-memory payout audit trail service for finance-grade traceability scaffolding."""
from __future__ import annotations

from datetime import datetime, timezone
from threading import Lock
import uuid
from typing import Any

VALID_LIFECYCLE_STATES = {"draft", "reviewed", "approved", "locked", "paid", "adjusted"}

_store: dict[str, dict[str, Any]] = {}
_store_lock = Lock()


def clear_store() -> None:
    """Clear all in-memory payout audit trail records (used on company switch)."""
    with _store_lock:
        _store.clear()


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _record_key(company_id: str, rep_id: str | None, period: str) -> str:
    stable = f"{company_id}:{rep_id or 'unassigned'}:{period}"
    return str(uuid.uuid5(uuid.NAMESPACE_URL, stable))


def upsert_payout_trace(
    *,
    company_id: str,
    period: str,
    rep_id: str | None,
    user_id: str | None,
    plan_id: str | None,
    rule_id: str | None,
    sales_credit_id: str | None,
    credited_amount: float,
    quota: float,
    attainment_pct: float,
    base_commission: float,
    accelerator_amount: float,
    spiff_amount: float,
    clawback_amount: float,
    final_payout: float,
    calculation_trace_json: dict[str, Any],
    source_records_json: dict[str, Any],
    computed_by: str,
) -> dict[str, Any]:
    payout_id = _record_key(company_id=company_id, rep_id=rep_id, period=period)

    with _store_lock:
        existing = _store.get(payout_id)
        version = int(existing.get("version", 0) if existing else 0) + 1
        lifecycle_state = existing.get("lifecycle_state", "draft") if existing else "draft"
        approval_status = existing.get("approval_status", "draft") if existing else "draft"

        # Locked payouts are immutable by policy. Keep original values but bump warning metadata.
        if existing and existing.get("is_locked"):
            return {
                **existing,
                "warnings": ["Payout is locked and cannot be recalculated. Use /payout-audit/{id}/adjust for corrections."],
            }

        record = {
            "payout_id": payout_id,
            "company_id": company_id,
            "rep_id": rep_id,
            "user_id": user_id,
            "plan_id": plan_id,
            "rule_id": rule_id,
            "sales_credit_id": sales_credit_id,
            "period": period,
            "credited_amount": float(credited_amount or 0.0),
            "quota": float(quota or 0.0),
            "attainment_pct": float(attainment_pct or 0.0),
            "base_commission": float(base_commission or 0.0),
            "accelerator_amount": float(accelerator_amount or 0.0),
            "spiff_amount": float(spiff_amount or 0.0),
            "clawback_amount": float(clawback_amount or 0.0),
            "final_payout": float(final_payout or 0.0),
            "calculation_trace_json": calculation_trace_json,
            "source_records_json": source_records_json,
            "computed_at": _utcnow(),
            "computed_by": computed_by,
            "approval_status": approval_status,
            "approved_by": existing.get("approved_by") if existing else None,
            "approved_at": existing.get("approved_at") if existing else None,
            "locked_at": existing.get("locked_at") if existing else None,
            "version": version,
            "is_locked": bool(existing.get("is_locked", False) if existing else False),
            "correction_ref": existing.get("correction_ref") if existing else None,
            "lifecycle_state": lifecycle_state,
        }
        _store[payout_id] = record
        return record


def list_payouts(company_id: str | None = None) -> list[dict[str, Any]]:
    with _store_lock:
        rows = list(_store.values())

    if company_id:
        rows = [r for r in rows if r.get("company_id") == company_id]

    rows.sort(key=lambda r: str(r.get("computed_at") or ""), reverse=True)
    return rows


def get_payout(payout_id: str) -> dict[str, Any] | None:
    with _store_lock:
        return _store.get(payout_id)


def _set_state(
    payout_id: str,
    state: str,
    actor: str,
    *,
    approval_status: str | None = None,
    lock_record: bool = False,
) -> dict[str, Any]:
    if state not in VALID_LIFECYCLE_STATES:
        raise ValueError(f"Unsupported lifecycle state '{state}'")

    with _store_lock:
        record = _store.get(payout_id)
        if not record:
            raise KeyError(payout_id)

        if record.get("is_locked") and state not in {"paid", "adjusted"}:
            raise ValueError("Locked payouts can only transition to paid or adjusted")

        record["lifecycle_state"] = state
        if approval_status:
            record["approval_status"] = approval_status
        if state == "approved":
            record["approved_by"] = actor
            record["approved_at"] = _utcnow()
        if lock_record or state == "locked":
            record["is_locked"] = True
            record["locked_at"] = _utcnow()
        record["version"] = int(record.get("version", 1)) + 1
        _store[payout_id] = record
        return record


def mark_reviewed(payout_id: str, actor: str) -> dict[str, Any]:
    return _set_state(payout_id, "reviewed", actor, approval_status="reviewed")


def approve_payout(payout_id: str, actor: str) -> dict[str, Any]:
    return _set_state(payout_id, "approved", actor, approval_status="approved")


def lock_payout(payout_id: str, actor: str) -> dict[str, Any]:
    return _set_state(payout_id, "locked", actor, approval_status="locked", lock_record=True)


def mark_paid(payout_id: str, actor: str) -> dict[str, Any]:
    """
    "paid" is a valid lifecycle_state (VALID_LIFECYCLE_STATES) and _set_state's
    own lock guard already allows locked -> paid, but nothing called this
    transition — a payout could reach "locked" through the API and then had
    no path to "paid" at all. Mirrors approve_payout/lock_payout exactly.
    """
    return _set_state(payout_id, "paid", actor, approval_status="paid")


def adjust_payout(
    payout_id: str,
    actor: str,
    adjustment_amount: float,
    reason: str,
) -> dict[str, Any]:
    with _store_lock:
        record = _store.get(payout_id)
        if not record:
            raise KeyError(payout_id)

        adjusted = dict(record)
        adjusted["final_payout"] = float(adjusted.get("final_payout", 0.0)) + float(adjustment_amount or 0.0)
        adjusted["clawback_amount"] = float(adjusted.get("clawback_amount", 0.0))
        adjusted["lifecycle_state"] = "adjusted"
        adjusted["approval_status"] = "adjusted"
        adjusted["correction_ref"] = payout_id
        adjusted["computed_by"] = actor
        adjusted["computed_at"] = _utcnow()
        adjusted["version"] = int(adjusted.get("version", 1)) + 1
        trace = dict(adjusted.get("calculation_trace_json") or {})
        trace["adjustment"] = {
            "amount": float(adjustment_amount or 0.0),
            "reason": reason,
            "actor": actor,
            "at": _utcnow(),
        }
        adjusted["calculation_trace_json"] = trace

        _store[payout_id] = adjusted
        return adjusted
