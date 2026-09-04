"""In-memory payout audit trail service for finance-grade traceability scaffolding."""
from __future__ import annotations

from datetime import datetime, timezone
from threading import Lock
import uuid
from typing import Any

VALID_LIFECYCLE_STATES = {"draft", "reviewed", "approved", "locked", "paid", "adjusted"}

_store: dict[str, dict[str, Any]] = {}
_store_lock = Lock()


def clear_store(company_id: str | None = None) -> None:
    """Clear in-memory payout audit trail records for one company (used when
    that company's dataset is (re)loaded, since its underlying PayoutRecord
    rows are about to be replaced and any old payout_ids are about to be
    invalid).

    `company_id=None` clears every company's records -- this used to be the
    *only* behavior, called unconditionally on every company load. Since
    records are already keyed by company_id (see `_record_key`/
    `list_payouts`), loading company A was wiping every approval, lock, and
    correction ever recorded for company B, C, ... too. Two companies are a
    supported, resident-at-once configuration (tests/test_tenancy_
    enforcement.py); this scopes the clear to match.
    """
    with _store_lock:
        if company_id is None:
            _store.clear()
            return
        for payout_id in [pid for pid, rec in _store.items() if rec.get("company_id") == company_id]:
            del _store[payout_id]


def seed_from_db_record(
    *,
    payout_id: str,
    company_id: str,
    period: str,
    user_id: str | None,
    rep_id: str | None,
    rep_name: str | None,
    plan_id: str | None,
    credited_amount: float,
    final_payout: float,
    confidence: float,
    fallback_used: bool,
) -> dict[str, Any]:
    """Register a real DB PayoutRecord as an actionable in-memory audit
    record, keyed by that record's own stable id -- not the rep/period hash
    `upsert_payout_trace` uses, since a PayoutRecord's id is already stable
    and callers (GET /payout-audit's list response) already hand it out as
    `payout_id`.

    Without this, a payout that nothing had explicitly run /payout/calculate
    or /payout/team-summary against existed only as a display-only row built
    from the DB, never registered here -- so review/approve/lock/pay on it
    raised KeyError (404 "Payout record not found"), always, regardless of
    company or period.

    Idempotent: if this payout_id is already tracked (a real lifecycle
    action already happened, or an earlier call already seeded it), the
    existing record -- and its lifecycle/approval state -- is returned
    unchanged rather than reset to draft.
    """
    with _store_lock:
        existing = _store.get(payout_id)
        if existing:
            return existing

        record = {
            "payout_id": payout_id,
            "company_id": company_id,
            "rep_id": rep_id,
            "rep_name": rep_name,
            "user_id": user_id,
            "plan_id": plan_id,
            "rule_id": None,
            "sales_credit_id": None,
            "period": period,
            "credited_amount": float(credited_amount or 0.0),
            "quota": 0.0,
            "attainment_pct": 0.0,
            "base_commission": float(final_payout or 0.0),
            "accelerator_amount": 0.0,
            "spiff_amount": 0.0,
            "clawback_amount": 0.0,
            "final_payout": float(final_payout or 0.0),
            "calculation_trace_json": {"mode": "db_fallback_seed"},
            "source_records_json": {
                "fallback_used": bool(fallback_used),
                "confidence": float(confidence or 0.0),
            },
            "computed_at": _utcnow(),
            "computed_by": "system-db-seed",
            "approval_status": "draft",
            "approved_by": None,
            "approved_at": None,
            "locked_at": None,
            "version": 1,
            "is_locked": False,
            "correction_ref": None,
            "lifecycle_state": "draft",
        }
        _store[payout_id] = record
        return record


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
