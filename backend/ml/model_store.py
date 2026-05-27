from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class ModelStore:
    """Simple JSON-backed model run metadata store for local/dev usage."""

    def __init__(self, path: str = "backend/ml/saved/model_runs.json") -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load_runs(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return []

    def append_run(self, run: dict[str, Any]) -> None:
        runs = self.load_runs()
        runs.append(run)
        self.path.write_text(json.dumps(runs, indent=2), encoding="utf-8")
