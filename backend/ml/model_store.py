from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class ModelStore:
    """Simple JSON-backed model run metadata store for local/dev usage.

    A fallback for GET /ml/model-runs, and the only source GET /ml/drift's
    baseline comes from -- used when the DB-backed ModelRunRecord table has
    nothing for the current tenant (see backend/routers/forecasting.py's
    _save_run, which writes both on every training run).

    Runs are tagged with the company they were trained for and filtered on
    read. A run with no company_id (from before this scoping existed, or a
    caller that didn't pass one) matches no company's filter -- treating an
    unattributable historical run as belonging to whichever tenant happens
    to ask is exactly the cross-tenant leak this exists to avoid: every
    company reading this file saw the exact same global run history,
    rubber-stamped with its own company_id, regardless of which company (if
    any) that run was actually trained for. Confirmed live: techo-solutions
    and insurex both got the identical 163-run history and the identical
    /ml/drift baseline (same trained_at, same training_rows) despite having
    entirely different datasets.
    """

    def __init__(self, path: str = "backend/ml/saved/model_runs.json") -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load_runs(self, company_id: str | None = None) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        try:
            runs = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return []
        if company_id is None:
            return runs
        return [r for r in runs if r.get("company_id") == company_id]

    def append_run(self, run: dict[str, Any], company_id: str | None = None) -> None:
        runs = self.load_runs()  # unfiltered -- need every existing run to append to and rewrite
        runs.append({**run, "company_id": company_id})
        self.path.write_text(json.dumps(runs, indent=2), encoding="utf-8")
