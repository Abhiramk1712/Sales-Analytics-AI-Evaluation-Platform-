"""
backend/agent/tools/ingestion_tools.py
=======================================
Agent tools that expose ingestion pipeline operations.
All functions return the standard {tool_name, status, data, warnings, sources} contract.
"""
from __future__ import annotations

import traceback
from typing import Any

from backend.ingestion.intelligent_ingestion import (
    inspect_source_directory,
    intelligent_ingest,
    evaluate_quality_gates,
    build_canonical_dataset,
)


async def discover_sources(source_dir: str) -> dict[str, Any]:
    """Inspect a source directory and return a manifest of detected CSV/PDF files."""
    try:
        inspection = inspect_source_directory(source_dir)
        return {
            "tool_name": "discover_sources",
            "status": "success",
            "data": {
                "source_dir": source_dir,
                "file_count": len(inspection.get("files", [])),
                "files": inspection.get("files", []),
                "inferred_entities": inspection.get("inferred_entities", []),
                "source_manifest": inspection.get("source_manifest", []),
            },
            "warnings": inspection.get("warnings", []),
            "sources": [source_dir],
        }
    except Exception as exc:
        return {
            "tool_name": "discover_sources",
            "status": "error",
            "data": {},
            "warnings": [f"Discovery failed: {exc}", traceback.format_exc()],
            "sources": [],
        }


async def check_data_quality(source_dir: str) -> dict[str, Any]:
    """Build canonical dataset without writing to DB and evaluate quality gates."""
    try:
        inspection = inspect_source_directory(source_dir)
        dataset, transform_warnings = build_canonical_dataset(inspection)
        quality = evaluate_quality_gates(dataset, transform_warnings)
        return {
            "tool_name": "check_data_quality",
            "status": "success",
            "data": quality,
            "warnings": transform_warnings,
            "sources": [source_dir],
        }
    except Exception as exc:
        return {
            "tool_name": "check_data_quality",
            "status": "error",
            "data": {},
            "warnings": [f"Quality check failed: {exc}"],
            "sources": [],
        }


async def execute_ingestion(
    source_dir: str,
    company_name: str,
    load_mode: str = "full_reload",
    reset_database: bool = True,
) -> dict[str, Any]:
    """Execute full ingestion: inspect → canonicalize → quality gate → load → audit."""
    try:
        result = await intelligent_ingest(
            source_dir=source_dir,
            company_name=company_name,
            reset_database=reset_database,
            load_mode=load_mode,
        )
        quality = result.get("quality_gate", {})
        return {
            "tool_name": "execute_ingestion",
            "status": "success" if not quality.get("blocked") else "partial",
            "data": {
                "company_name": result.get("company_name"),
                "company_dir": result.get("company_dir"),
                "db_rows_loaded": result.get("db_rows_loaded", {}),
                "load_mode": load_mode,
                "quality_gate": quality,
                "source_manifest": result.get("source_manifest", []),
            },
            "warnings": result.get("warnings", []),
            "sources": [source_dir],
        }
    except Exception as exc:
        return {
            "tool_name": "execute_ingestion",
            "status": "error",
            "data": {},
            "warnings": [f"Ingestion failed: {exc}", traceback.format_exc()],
            "sources": [],
        }
