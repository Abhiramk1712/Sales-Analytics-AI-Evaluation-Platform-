"""
Lightweight in-process workflow store.

Stores workflow results by workflow_id in memory with an optional JSON
persistence path. This is intentionally simple — no Celery/RQ required.

Future: replace with persistent DB table or task queue.
"""
from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime, UTC
from pathlib import Path
from typing import Any

_LOCK = threading.Lock()
_STORE: dict[str, dict[str, Any]] = {}
_PERSIST_PATH: Path | None = None


def configure_persistence(path: Path) -> None:
    """Optionally persist the store to a JSON file on each write."""
    global _PERSIST_PATH
    _PERSIST_PATH = path
    if path.exists():
        try:
            with open(path) as f:
                data = json.load(f)
            with _LOCK:
                _STORE.update(data)
        except Exception:
            pass  # corrupt file — start fresh


def create_workflow(
    workflow_id: str | None = None,
    workflow_type: str | None = None,
    request_payload: dict[str, Any] | None = None,
    *,
    pipeline: str | None = None,
    period: str | None = None,
    company_id: str | None = None,
) -> str:
    """
    Create a new workflow entry and return its ID.

    Accepts either the old positional API (workflow_type, request_payload)
    or the new keyword API (workflow_id, pipeline, period, company_id).
    """
    wid = workflow_id or str(uuid.uuid4())
    effective_pipeline = pipeline or workflow_type or "unknown"
    effective_payload = request_payload or {}

    entry: dict[str, Any] = {
        "workflow_id": wid,
        "pipeline": effective_pipeline,
        "workflow_type": effective_pipeline,  # backward compat
        "period": period,
        "company_id": company_id,
        "status": "running",
        "created_at": datetime.now(UTC).isoformat(),
        "completed_at": None,
        "request": effective_payload,
        "result": None,
        "errors": [],
    }
    with _LOCK:
        _STORE[wid] = entry
        _maybe_persist()
    return wid


def complete_workflow(
    workflow_id: str,
    result: dict[str, Any],
    status: str = "completed",
    steps_completed: list[str] | None = None,
) -> None:
    """Mark a workflow complete with its result."""
    with _LOCK:
        if workflow_id not in _STORE:
            return
        _STORE[workflow_id].update(
            status=status,
            completed_at=datetime.now(UTC).isoformat(),
            result=result,
            steps_completed=steps_completed or [],
        )
        _maybe_persist()


def fail_workflow(workflow_id: str, error: str | None = None, errors: list[str] | None = None) -> None:
    """Mark a workflow as failed."""
    all_errors = errors or ([error] if error else [])
    with _LOCK:
        if workflow_id not in _STORE:
            return
        _STORE[workflow_id].update(
            status="failed",
            completed_at=datetime.now(UTC).isoformat(),
            result={"error": error},
            errors=all_errors,
        )
        _maybe_persist()


def get_workflow(workflow_id: str) -> dict[str, Any] | None:
    """Retrieve a workflow by ID."""
    with _LOCK:
        return dict(_STORE.get(workflow_id, {})) or None


def list_workflows(limit: int = 20) -> list[dict[str, Any]]:
    """Return the most recent workflows."""
    with _LOCK:
        items = sorted(_STORE.values(), key=lambda x: x.get("created_at", ""), reverse=True)
        return [dict(w) for w in items[:limit]]


def _maybe_persist() -> None:
    """Write store to disk if a persistence path is configured. Caller holds lock."""
    if _PERSIST_PATH is not None:
        try:
            _PERSIST_PATH.write_text(json.dumps(_STORE, indent=2, default=str))
        except Exception:
            pass
