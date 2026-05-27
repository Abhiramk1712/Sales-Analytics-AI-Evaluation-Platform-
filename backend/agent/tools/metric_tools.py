"""
Metric registry tools for the AI agent.
"""
from __future__ import annotations

from typing import Any

from backend.metrics.service import get_metrics_service


def _result(tool_name: str, status: str, data: Any, warnings: list[str], sources: list[str]) -> dict[str, Any]:
    return {
        "tool_name": tool_name,
        "status": status,
        "data": data,
        "warnings": warnings,
        "sources": sources,
    }


def list_metrics() -> dict[str, Any]:
    service = get_metrics_service()
    metrics = service.list_metrics()
    return _result("list_metrics", "success", metrics, [], ["metrics_registry"])


def get_metric_definition(metric_name: str) -> dict[str, Any]:
    service = get_metrics_service()
    try:
        metric = service.get_metric_info(metric_name)
        return _result("get_metric_definition", "success", metric, [], ["metrics_registry"])
    except ValueError:
        return _result(
            "get_metric_definition",
            "error",
            None,
            [f"Unknown metric '{metric_name}'. Use list_metrics to discover supported metrics."],
            ["metrics_registry"],
        )
