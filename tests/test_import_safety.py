from __future__ import annotations

import importlib


def test_pure_modules_import_without_database_connection() -> None:
    modules = [
        "backend.ml.forecasting_engine",
        "backend.ml.text_features",
        "backend.statistics.sales_drivers",
        "backend.ml.evaluation",
    ]

    for module_name in modules:
        module = importlib.import_module(module_name)
        assert module is not None
