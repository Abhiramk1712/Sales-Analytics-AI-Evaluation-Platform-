from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends

from backend.auth.dependencies import require_permission
from backend.grading.enterprise_grader import EnterpriseGrader

router = APIRouter(
    prefix="/grading",
    tags=["Grading"],
    dependencies=[Depends(require_permission("view_data_quality"))],
)


@router.get("/enterprise-readiness")
async def enterprise_readiness():
    root = Path(__file__).resolve().parents[2]
    grader = EnterpriseGrader(str(root))
    return grader.run()
