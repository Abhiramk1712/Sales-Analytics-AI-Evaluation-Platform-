"""
backend/metrics/registry.py
===========================
Metrics registry and service for looking up metric definitions
"""
from typing import Optional, Dict
from backend.metrics.definitions import MetricDefinition, get_all_metric_definitions


class MetricsRegistry:
    """
    Registry for metric definitions.
    Provides centralized lookup of metric formulas, required fields, and metadata.
    """
    
    def __init__(self):
        """Initialize registry with built-in metrics."""
        self._metrics: Dict[str, MetricDefinition] = get_all_metric_definitions()
    
    def register(self, metric: MetricDefinition) -> None:
        """Register a new metric definition."""
        if metric.name in self._metrics:
            raise ValueError(f"Metric '{metric.name}' already exists")
        self._metrics[metric.name] = metric
    
    def get(self, name: str) -> Optional[MetricDefinition]:
        """
        Get a metric definition by name.
        
        Args:
            name: Metric name
        
        Returns:
            MetricDefinition or None if not found
        """
        return self._metrics.get(name)
    
    def get_required(self, name: str) -> MetricDefinition:
        """
        Get a metric definition, raising an error if not found.
        
        Args:
            name: Metric name
        
        Returns:
            MetricDefinition
        
        Raises:
            ValueError: If metric not found
        """
        metric = self._metrics.get(name)
        if not metric:
            raise ValueError(f"Unknown metric: '{name}'. Available: {list(self._metrics.keys())}")
        return metric
    
    def exists(self, name: str) -> bool:
        """Check if a metric is registered."""
        return name in self._metrics
    
    def list_all(self) -> list[MetricDefinition]:
        """List all metrics."""
        return list(self._metrics.values())
    
    def list_by_grain(self, grain: str) -> list[MetricDefinition]:
        """List all metrics at a specific grain level."""
        return [m for m in self._metrics.values() if m.grain == grain]
    
    def list_by_owner(self, owner: str) -> list[MetricDefinition]:
        """List all metrics owned by a team."""
        return [m for m in self._metrics.values() if m.owner == owner]
    
    def get_required_fields(self, metric_names: list[str]) -> set[str]:
        """
        Get union of all required fields for a list of metrics.
        
        Args:
            metric_names: List of metric names
        
        Returns:
            Set of required field names
        """
        fields = set()
        for name in metric_names:
            metric = self.get_required(name)
            fields.update(metric.required_fields)
        return fields


# Global registry instance
_global_registry = MetricsRegistry()


def get_global_registry() -> MetricsRegistry:
    """Get the global metrics registry."""
    return _global_registry
