"""
Workflow management routes.

POST /workflows/sales-performance  — run full sales performance pipeline
GET  /workflows/{workflow_id}       — get a specific workflow result
GET  /workflows                     — list recent workflows
GET  /workflows/jobs/{job_id}       — get background job status
"""
from __future__ import annotations

import uuid as _uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.workflows import store as workflow_store

router = APIRouter(prefix="/workflows", tags=["Workflows"])

# ── Lightweight in-memory job status store for demo mode ──────────────────
# TODO: Replace with DB-backed JobStatus model or Celery/RQ in production.
_job_store: dict[str, dict[str, Any]] = {}


def _create_job(job_type: str, metadata: dict | None = None) -> str:
    job_id = str(_uuid.uuid4())
    _job_store[job_id] = {
        "job_id": job_id,
        "job_type": job_type,
        "status": "queued",
        "progress": 0,
        "metadata": metadata or {},
        "error_message": None,
        "started_at": None,
        "finished_at": None,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    return job_id


def _update_job(job_id: str, **kwargs: Any) -> None:
    if job_id in _job_store:
        _job_store[job_id].update(kwargs)


@router.get("/jobs/{job_id}")
async def get_job_status(job_id: str) -> dict[str, Any]:
    """Return status of a background job."""
    job = _job_store.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


class SalesPerformanceRequest(BaseModel):
    period: str | None = Field(None, description="e.g. '2025-Q2', 'this quarter'")
    options: dict[str, Any] = Field(default_factory=dict)


@router.post("/sales-performance")
async def run_sales_performance(
    req: SalesPerformanceRequest,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """
    Run full sales-performance pipeline (synchronous).

    Stores result under a workflow_id so it can be retrieved later via
    GET /workflows/{workflow_id}.

    Future: move to async task queue (Celery/RQ) without changing this contract.
    """
    from backend.agent.workflows.sales_performance_pipeline import run_sales_performance_pipeline

    wid = workflow_store.create_workflow(
        pipeline="sales_performance",
        period=req.period,
    )
    try:
        result = await run_sales_performance_pipeline(
            db=db,
            period=req.period,
            options=req.options,
        )
        # Attach lineage metadata
        result.setdefault("workflow_id", wid)
        result.setdefault("lineage", {
            "workflow_type": "sales_performance",
            "period": req.period,
            "tables_used": ["revenue", "deals", "quotas", "reps", "plans", "payouts"],
            "generated_at": result.get("generated_at"),
        })
        status = result.get("status", "success")
        workflow_store.complete_workflow(wid, result, status=status, steps_completed=result.get("steps_completed", []))
        return {"workflow_id": wid, **result}
    except Exception as exc:
        workflow_store.fail_workflow(wid, error=str(exc))
        raise HTTPException(status_code=500, detail=f"Workflow failed: {exc}") from exc


@router.get("")
async def list_workflows(limit: int = 20) -> dict[str, Any]:
    """Return most recent workflow runs."""
    return {"workflows": workflow_store.list_workflows(limit=limit)}


@router.get("/{workflow_id}")
async def get_workflow(workflow_id: str) -> dict[str, Any]:
    """Return status and result for a specific workflow."""
    entry = workflow_store.get_workflow(workflow_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="Workflow not found")
    return entry


class SalesTrackerRequest(BaseModel):
    period: str | None = Field(None, description="e.g. '2025-Q1', 'this quarter'")
    company_id: str | None = Field(None)


@router.post("/sales-tracker/run")
async def run_sales_tracker(
    req: SalesTrackerRequest,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """
    Run the canonical end-to-end Sales Tracker workflow.
    Steps: performance → data quality → deal risk → clustering → forecast → payouts → report.
    """
    from backend.workflows.sales_tracker_workflow import run_sales_tracker_workflow
    result = await run_sales_tracker_workflow(db, period=req.period, company_id=req.company_id)
    return result


@router.get("/{workflow_id}/audit")
async def get_workflow_audit(workflow_id: str) -> dict[str, Any]:
    """Return audit trail for a specific workflow."""
    entry = workflow_store.get_workflow(workflow_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="Workflow not found")
    result = entry.get("result", {})
    return {
        "workflow_id": workflow_id,
        "pipeline": entry.get("pipeline"),
        "status": entry.get("status"),
        "audit_trail": result.get("audit_trail", []),
        "steps_completed": result.get("steps_completed", []),
        "steps_failed": result.get("steps_failed", []),
        "warnings": result.get("warnings", []),
        "completed_at": entry.get("completed_at"),
    }
