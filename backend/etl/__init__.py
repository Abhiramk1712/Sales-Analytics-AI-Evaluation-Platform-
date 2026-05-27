"""ETL package for medallion-style sales data workflows."""

from backend.etl.pipeline import run_etl_pipeline

__all__ = ["run_etl_pipeline"]
