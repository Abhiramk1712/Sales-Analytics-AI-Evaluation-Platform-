"""ETL orchestration endpoints for admin workflows."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from backend.auth.dependencies import require_permission
from backend.etl.pipeline import run_etl_pipeline
from backend.routers.ingestion import resolve_source_dir

router = APIRouter(
    prefix="/etl",
    tags=["ETL"],
    dependencies=[Depends(require_permission("run_ingestion"))],
)


@router.post("/run")
def run_etl(source_dir: str = Query(..., description="Path to source CSV folder")):
    # Same confinement as the ingestion endpoints: source_dir is caller-supplied.
    resolved = str(resolve_source_dir(source_dir))
    try:
        result = run_etl_pipeline(resolved)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"ETL failed: {exc}") from exc

    return {
        "generated_at": result.generated_at,
        "bronze_tables": {k: v["row_count"] for k, v in result.bronze.items()},
        "quality": result.quality,
        "gold_marts": {
            name: {
                "row_count": meta["metadata"]["row_count"],
                "data_quality_score": meta["metadata"]["data_quality_score"],
                "warnings": meta["metadata"].get("warnings", []),
            }
            for name, meta in result.gold.items()
        },
        "feature_store_tables": list(result.feature_store.keys()),
    }
