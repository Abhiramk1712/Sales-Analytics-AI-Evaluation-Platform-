"""Forecasting lab package exports."""

from .datasets import TARGET_REGISTRY, list_supported_targets
from .service import compare_models_for_target, run_forecast_for_target

__all__ = [
    "TARGET_REGISTRY",
    "list_supported_targets",
    "compare_models_for_target",
    "run_forecast_for_target",
]
