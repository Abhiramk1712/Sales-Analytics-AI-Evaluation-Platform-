"""
Evidence verifier for agent safety and grounding.
"""
from __future__ import annotations

from typing import Any

from backend.agent.state import AgentState
from backend.metrics import get_global_registry


class EvidenceVerifier:
    def __init__(self) -> None:
        self.metrics_registry = get_global_registry()

    def verify_state(self, state: AgentState) -> tuple[bool, list[str]]:
        warnings = list(state.warnings)

        if not state.tools_called:
            warnings.append("No tools were called to gather evidence")
            return False, warnings

        if not state.evidence_results:
            warnings.append("No evidence was collected")
            return False, warnings

        statuses = [r.get("status") for r in state.evidence_results]
        if all(s == "error" for s in statuses):
            warnings.append("All tools returned errors")
            return False, warnings

        non_empty = False
        for result in state.evidence_results:
            data = result.get("data")
            if isinstance(data, dict) and data:
                non_empty = True
                break
            if isinstance(data, list) and len(data) > 0:
                non_empty = True
                break
            if isinstance(data, (str, int, float)) and data not in ("", 0, 0.0):
                non_empty = True
                break

        if not non_empty:
            warnings.append("Evidence payloads are empty")
            return False, warnings

        return True, warnings

    def extract_metric_mentions(self, message: str) -> list[str]:
        text = (message or "").lower()
        metrics = []
        for metric in self.metrics_registry.list_all():
            if metric.name in text or metric.name.replace("_", " ") in text:
                metrics.append(metric.name)
        return metrics
