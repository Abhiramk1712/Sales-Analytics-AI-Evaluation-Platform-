"""
main.py — FastAPI application entry point
"""
import time
import uuid
import logging

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from contextlib import asynccontextmanager
from backend.auth.tenant import extract_company_hint
from backend.company_context import ensure_company_loaded, get_active_company, wait_for_company_load_completion
from backend.database import get_engine, Base
from backend.config import settings
from backend.routers import analytics, forecasting, agent, reports, grading, ingestion, data_quality, payout, etl
from backend.routers import workflows as workflows_router
from backend.routers.plans import router as plans_router, territory_router
from backend.routers.payout_audit import router as payout_audit_router

logger = logging.getLogger(__name__)

SCOPED_ROUTE_PREFIXES = (
    "/analytics",
    "/payout",
    "/ml",
    "/reports",
    "/data-quality",
    "/plans",
    "/territories",
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Create tables on startup when AUTO_CREATE_TABLES is enabled (default: demo/dev mode)
    if settings.AUTO_CREATE_TABLES:
        engine = get_engine()
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    yield


app = FastAPI(
    title=settings.APP_TITLE,
    version=settings.APP_VERSION,
    description="AI-powered sales analytics platform — Master's project",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(analytics.router)
app.include_router(forecasting.router)
app.include_router(agent.router)
app.include_router(reports.router)
app.include_router(grading.router)
app.include_router(ingestion.router)
app.include_router(data_quality.router)
app.include_router(payout.router)
app.include_router(payout_audit_router)
app.include_router(etl.router)
app.include_router(workflows_router.router)
app.include_router(plans_router)
app.include_router(territory_router)


@app.middleware("http")
async def company_alignment_middleware(request: Request, call_next):
    path = request.url.path
    if any(path.startswith(prefix) for prefix in SCOPED_ROUTE_PREFIXES):
        request_company = extract_company_hint(request)
        if not request_company:
            # Avoid read-vs-reload races: wait for in-flight company load to complete
            # before resolving implicit company context from process state.
            await wait_for_company_load_completion()

        active = get_active_company()
        company = (
            request_company
            or active
            or (settings.DEMO_DEFAULT_COMPANY if settings.DEMO_MODE else None)
        )

        # Loading a company is destructive — it drops and recreates every table
        # (data_generator._load_csvs_into_database). A read request must never be
        # able to trigger that, and `company` here can come straight from an
        # unauthenticated header or query parameter. So the only implicit load
        # permitted is the one-time cold-start bootstrap of the *configured*
        # demo default; switching companies is an explicit, deliberate call to
        # POST /ingestion/load-company.
        if company and company != active:
            if active is not None or company != settings.DEMO_DEFAULT_COMPANY:
                return JSONResponse(
                    status_code=409,
                    content={
                        "detail": (
                            f"Company '{company}' is not the active dataset"
                            + (f" (active: '{active}')." if active else ".")
                            + " Load it explicitly via POST /ingestion/load-company —"
                            " this rebuilds the database and is never done implicitly."
                        )
                    },
                )
            try:
                await ensure_company_loaded(company)
            except FileNotFoundError as exc:
                return JSONResponse(status_code=404, content={"detail": str(exc)})
            except Exception as exc:
                logger.exception("Company alignment failed for '%s': %s", company, exc)
                return JSONResponse(status_code=500, content={"detail": "Failed to align company context"})

    return await call_next(request)


# ── Observability: request timing + correlation ID ────────────────────────

@app.middleware("http")
async def request_timing_middleware(request: Request, call_next):
    correlation_id = request.headers.get("X-Correlation-ID", str(uuid.uuid4()))
    start = time.perf_counter()
    response = await call_next(request)
    elapsed_ms = round((time.perf_counter() - start) * 1000, 2)
    response.headers["X-Correlation-ID"] = correlation_id
    response.headers["X-Response-Time-Ms"] = str(elapsed_ms)
    logger.debug("%s %s → %s in %sms [%s]", request.method, request.url.path, response.status_code, elapsed_ms, correlation_id)
    return response


# ── Global error handler ──────────────────────────────────────────────────

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    correlation_id = request.headers.get("X-Correlation-ID", str(uuid.uuid4()))
    logger.exception("Unhandled error [%s]: %s", correlation_id, exc)
    # Never expose raw exception details to the client
    return JSONResponse(
        status_code=500,
        content={
            "detail": "Internal server error",
            "correlation_id": correlation_id,
        },
    )


@app.get("/health")
async def health():
    return {"status": "ok", "version": settings.APP_VERSION}


@app.get("/")
async def root():
    return {
        "message": "Sales Analytics AI API",
        "docs": "/docs",
        "endpoints": {
            "analytics": "/analytics/kpis | /analytics/revenue/monthly | /analytics/reps/performance | /analytics/pipeline/stages",
            "ml":        "/ml/forecast/revenue | /ml/score/deals | /ml/cluster/reps",
            "agent":     "/agent/chat",
            "reports":   "/reports/types | /reports/generate",
            "grading":   "/grading/enterprise-readiness",
            "ingestion": "/ingestion/inspect | /ingestion/intelligent-load",
            "data_quality": "/data-quality/summary | /data-quality/checks",
            "payout": "/payout/calculate | /payout/team-summary | /payout/statements/{rep_id} | /payout/config | /payout/quota-suggestions/{rep_id} | /payout/quota-fairness",
            "etl": "/etl/run",
        }
    }
