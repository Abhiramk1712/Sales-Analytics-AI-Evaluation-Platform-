"""Intelligent ingestion endpoints for arbitrary company CSV/PDF sources."""
from __future__ import annotations

import uuid
from pathlib import Path

from pydantic import BaseModel, Field
from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from backend.company_context import get_active_company, load_company_into_context, set_active_company
from backend.data_generator import load_company_dataset
from backend.ingestion.intelligent_ingestion import intelligent_ingest, inspect_source_directory
from backend.ingestion.manifest_loader import build_manifest_canonical_dataset
from backend.ingestion.source_registry import get_manifest_registry

router = APIRouter(prefix="/ingestion", tags=["Ingestion"])


LOAD_MODES = {"full_reload", "upsert", "append"}


class IntelligentIngestionRequest(BaseModel):
    source_dir: str = Field(..., description="Directory containing source CSV/PDF files")
    company_name: str = Field(..., description="Logical company name for output folder")
    reset_database: bool = Field(default=True, description="Drop and recreate DB tables before load (only applies to full_reload mode)")
    load_mode: str = Field(default="full_reload", description="Load mode: full_reload | upsert | append")
    use_manifest: bool = Field(default=True, description="Use manifest-driven mapping pipeline with fallback")
    manifest_name: str = Field(default="sales_schema", description="Manifest name")
    manifest_version: str = Field(default="v1", description="Manifest version")


class LoadCompanyRequest(BaseModel):
    company_name: str = Field(..., description="Company name/folder under companies/")


@router.get("/companies")
def list_companies():
    companies_dir = Path("companies")
    if not companies_dir.exists() or not companies_dir.is_dir():
        return {"companies": []}

    required_files = {"teams.csv", "reps.csv", "accounts.csv", "deals.csv"}
    companies = []
    for entry in sorted(companies_dir.iterdir()):
        if not entry.is_dir():
            continue
        existing = {p.name for p in entry.glob("*.csv")}
        if not required_files.issubset(existing):
            continue
        companies.append(
            {
                "name": entry.name,
                "path": str(entry),
                "csv_count": len(existing),
            }
        )

    return {"companies": companies}


@router.post("/inspect")
def inspect_source(req: IntelligentIngestionRequest):
    try:
        result = inspect_source_directory(req.source_dir)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:  # pragma: no cover
        raise HTTPException(status_code=400, detail=f"Inspection failed: {exc}") from exc
    return result


@router.post("/inspect-v2")
def inspect_source_v2(req: IntelligentIngestionRequest):
    """Manifest-aware inspection preview without loading to DB."""
    try:
        inspection = inspect_source_directory(req.source_dir)
        dataset, warnings, manifest_details = build_manifest_canonical_dataset(
            inspection=inspection,
            manifest_name=req.manifest_name,
            manifest_version=req.manifest_version,
        )
        counts = {k: len(v) for k, v in dataset.items()}
        return {
            "inspection": inspection,
            "manifest_details": manifest_details,
            "canonical_counts": counts,
            "warnings": warnings,
            "relationship_resolution": manifest_details.get("relationship_resolution", {}) if manifest_details else {},
            "canonical_lineage": manifest_details.get("canonical_lineage", {}) if manifest_details else {},
            "quality_preview": {
                "has_reps": counts.get("reps", 0) > 0,
                "has_deals": counts.get("deals", 0) > 0,
                "has_revenue": counts.get("revenue", 0) > 0,
            },
        }
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:  # pragma: no cover
        raise HTTPException(status_code=400, detail=f"Manifest inspection failed: {exc}") from exc


@router.post("/dry-run-load")
def dry_run_load(req: IntelligentIngestionRequest):
    """Dry-run mapping and quality preview without DB writes."""
    try:
        inspection = inspect_source_directory(req.source_dir)
        if req.use_manifest:
            dataset, warnings, manifest_details = build_manifest_canonical_dataset(
                inspection=inspection,
                manifest_name=req.manifest_name,
                manifest_version=req.manifest_version,
            )
        else:
            from backend.ingestion.intelligent_ingestion import build_canonical_dataset
            dataset, warnings = build_canonical_dataset(inspection)
            manifest_details = None

        counts = {k: len(v) for k, v in dataset.items()}
        would_generate = [
            name for name in ("teams", "quotas", "revenue") if counts.get(name, 0) > 0
        ]
        return {
            "would_load": counts,
            "would_generate_domains": would_generate,
            "manifest_details": manifest_details,
            "relationship_resolution": manifest_details.get("relationship_resolution", {}) if manifest_details else {},
            "canonical_lineage": manifest_details.get("canonical_lineage", {}) if manifest_details else {},
            "warnings": warnings,
        }
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:  # pragma: no cover
        raise HTTPException(status_code=400, detail=f"Dry-run failed: {exc}") from exc


@router.post("/intelligent-load")
async def intelligent_load(req: IntelligentIngestionRequest):
    if req.load_mode not in LOAD_MODES:
        raise HTTPException(status_code=400, detail=f"Invalid load_mode '{req.load_mode}'. Must be one of: {sorted(LOAD_MODES)}")
    try:
        result = await intelligent_ingest(
            source_dir=req.source_dir,
            company_name=req.company_name,
            reset_database=req.reset_database,
            load_mode=req.load_mode,
            use_manifest=req.use_manifest,
            manifest_name=req.manifest_name,
            manifest_version=req.manifest_version,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # pragma: no cover
        raise HTTPException(status_code=500, detail=f"Intelligent ingestion failed: {exc}") from exc
    set_active_company(result.get("company_name") or req.company_name)
    return result


def _write_uploaded_sources(files: list[UploadFile]) -> Path:
    upload_root = Path("companies") / "_uploads"
    upload_root.mkdir(parents=True, exist_ok=True)

    run_dir = upload_root / f"ingestion-{uuid.uuid4().hex}"
    run_dir.mkdir(parents=True, exist_ok=True)

    allowed_ext = {".csv", ".pdf", ".xlsx", ".xls"}
    for file in files:
        suffix = Path(file.filename or "").suffix.lower()
        if suffix not in allowed_ext:
            raise ValueError(f"Unsupported file type for '{file.filename}'. Supported: CSV, PDF")

        out_path = run_dir / Path(file.filename or f"upload-{uuid.uuid4().hex}{suffix}").name
        content = file.file.read()
        out_path.write_bytes(content)

    return run_dir


@router.post("/upload-intelligent-load")
async def upload_and_intelligent_load(
    company_name: str = Form(..., description="Logical company name for output folder"),
    reset_database: bool = Form(True, description="Drop and recreate DB tables before load"),
    load_mode: str = Form("full_reload", description="Load mode: full_reload | upsert | append"),
    use_manifest: bool = Form(True, description="Use manifest-driven mapping pipeline with fallback"),
    manifest_name: str = Form("sales_schema", description="Manifest name"),
    manifest_version: str = Form("v1", description="Manifest version"),
    files: list[UploadFile] = File(..., description="CSV/PDF files to ingest"),
):
    if not files:
        raise HTTPException(status_code=400, detail="No files supplied")
    if load_mode not in LOAD_MODES:
        raise HTTPException(status_code=400, detail=f"Invalid load_mode '{load_mode}'.")

    try:
        source_dir = _write_uploaded_sources(files)
        result = await intelligent_ingest(
            source_dir=str(source_dir),
            company_name=company_name,
            reset_database=reset_database,
            load_mode=load_mode,
            use_manifest=use_manifest,
            manifest_name=manifest_name,
            manifest_version=manifest_version,
        )
        set_active_company(result.get("company_name") or company_name)
        result["upload_source_dir"] = str(source_dir)
        result["uploaded_files"] = [f.filename for f in files]
        return result
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # pragma: no cover
        raise HTTPException(status_code=500, detail=f"Upload ingestion failed: {exc}") from exc


@router.post("/load-company")
async def load_company(req: LoadCompanyRequest):
    try:
        counts = await load_company_into_context(
            req.company_name,
            force_reload=False,
            loader=load_company_dataset,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:  # pragma: no cover
        raise HTTPException(status_code=500, detail=f"Failed to load company dataset: {exc}") from exc

    set_active_company(req.company_name)

    return {
        "company_name": req.company_name,
        "db_rows_loaded": counts,
    }


@router.get("/active-company")
def active_company_context():
    return {"active_company": get_active_company()}


# ============================================================================
# Manifest Contract Validation Endpoints (Phase 1b)
# ============================================================================

class ManifestValidationRequest(BaseModel):
    manifest_name: str = Field("sales_schema", description="Manifest name")
    version: str = Field("v1", description="Manifest version")


class SchemaColumnMapping(BaseModel):
    table_name: str
    columns: list[str]


class DriftDetectionRequest(BaseModel):
    manifest_name: str = Field("sales_schema", description="Manifest name")
    version: str = Field("v1", description="Manifest version")
    actual_schema: dict[str, list[str]] = Field(..., description="Dict mapping table_name -> list of column names")


@router.get("/manifest/list")
def list_available_manifests():
    """List all available manifest schemas."""
    try:
        registry = get_manifest_registry()
        manifests = registry.list_manifests()
        return {
            "manifests": manifests,
            "count": len(manifests),
        }
    except Exception as exc:  # pragma: no cover
        raise HTTPException(status_code=500, detail=f"Failed to list manifests: {exc}") from exc


@router.post("/manifest/validate")
def validate_manifest(req: ManifestValidationRequest):
    """
    Validate a manifest schema.
    
    Returns:
    - valid: bool — whether manifest is valid
    - errors: list[str] — validation errors (if any)
    - warnings: list[str] — validation warnings (if any)
    - load_order: list[str] — topological load order for tables
    - tables_count: int — number of tables
    - relationships_count: int — number of relationships
    - hard_required_tables: list[str] — required tables
    - optional_tables: list[str] — optional tables
    """
    try:
        registry = get_manifest_registry()
        report = registry.validate_manifest(req.manifest_name, req.version)
        return report
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # pragma: no cover
        raise HTTPException(status_code=500, detail=f"Manifest validation failed: {exc}") from exc


@router.post("/manifest/load")
def load_manifest(req: ManifestValidationRequest):
    """
    Load a manifest schema (returns full schema object).
    """
    try:
        registry = get_manifest_registry()
        manifest = registry.load_manifest(req.manifest_name, req.version)
        return {
            "name": manifest.name,
            "version": manifest.version,
            "description": manifest.description,
            "created_at": manifest.created_at,
            "last_updated": manifest.last_updated,
            "tables": {name: {
                "table_name": table.table_name,
                "required_level": table.required_level,
                "columns_count": len(table.columns),
                "primary_key": table.primary_key,
            } for name, table in manifest.tables.items()},
            "relationships_count": len(manifest.relationships),
            "hard_required_tables": manifest.get_hard_required_tables(),
            "optional_tables": manifest.get_optional_tables(),
        }
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # pragma: no cover
        raise HTTPException(status_code=500, detail=f"Failed to load manifest: {exc}") from exc


@router.post("/manifest/detect-drift")
def detect_schema_drift(req: DriftDetectionRequest):
    """
    Detect schema drift between manifest and actual data.
    
    Returns:
    - {table_name: {
        manifest_columns: [...],
        actual_columns: [...],
        missing_in_actual: [...],  # columns in manifest but not in actual data
        extra_in_actual: [...],     # columns in actual but not in manifest
        drift_detected: bool
      }
    """
    try:
        registry = get_manifest_registry()
        drift_report = registry.detect_schema_drift(
            req.manifest_name,
            req.actual_schema,
            req.version
        )
        return {
            "manifest": f"{req.manifest_name}:{req.version}",
            "drift_detected": any(v.get("drift_detected", False) for v in drift_report.values()),
            "tables": drift_report,
        }
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # pragma: no cover
        raise HTTPException(status_code=500, detail=f"Drift detection failed: {exc}") from exc
