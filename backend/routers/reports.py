from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.auth.dependencies import require_permission
from backend.auth.tenant import get_tenant_context
from backend.reports.report_generator import ReportGenerator
from backend.reports.types import REPORT_TYPES, REPORT_TYPE_LABELS

router = APIRouter(
    prefix="/reports",
    tags=["Reports"],
    dependencies=[Depends(require_permission("view_dashboard")), Depends(get_tenant_context)],
)

SUPPORTED_REPORT_TYPES = REPORT_TYPES  # backward-compat alias
KNOWLEDGE_BASE_DIR = Path(__file__).resolve().parents[2] / "docs" / "knowledge_base"


class ReportRequest(BaseModel):
    report_type: str
    period: str
    audience: str = "Sales Leadership"
    filters: dict[str, Any] = Field(default_factory=dict)


@router.get("/types")
async def report_types() -> dict[str, Any]:
    return {
        "report_types": REPORT_TYPES,
        "labels": REPORT_TYPE_LABELS,
    }


@router.post("/generate")
async def generate_report(
    req: ReportRequest,
    db: AsyncSession = Depends(get_db),
    _: Any = Depends(require_permission("generate_reports")),
) -> dict[str, Any]:
    if req.report_type not in SUPPORTED_REPORT_TYPES:
        raise HTTPException(status_code=400, detail=f"Unsupported report type '{req.report_type}'")

    try:
        return await ReportGenerator.generate_report(
            db=db,
            report_type=req.report_type,
            period=req.period,
            audience=req.audience,
            filters=req.filters,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/knowledge-base")
async def list_knowledge_base_documents() -> dict[str, Any]:
    if not KNOWLEDGE_BASE_DIR.exists():
        return {"documents": []}

    documents = sorted([p.name for p in KNOWLEDGE_BASE_DIR.glob("*.md")])
    return {"documents": documents}


@router.get("/knowledge-base/{document_name}")
async def get_knowledge_base_document(document_name: str) -> dict[str, Any]:
    safe_name = Path(document_name).name
    if safe_name != document_name or not safe_name.endswith(".md"):
        raise HTTPException(status_code=400, detail="Invalid document name")

    doc_path = KNOWLEDGE_BASE_DIR / safe_name
    if not doc_path.exists() or not doc_path.is_file():
        raise HTTPException(status_code=404, detail="Knowledge base document not found")

    return {
        "document_name": safe_name,
        "content": doc_path.read_text(encoding="utf-8"),
    }
