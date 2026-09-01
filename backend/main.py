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
from backend.auth.tokens import assert_auth_configured
from backend.tenancy import tenant_scope
from backend.database import get_engine, Base
from backend.config import settings
from backend.routers import analytics, forecasting, agent, reports, grading, ingestion, data_quality, payout, etl
from backend.routers import workflows as workflows_router
from backend.routers.plans import router as plans_router, territory_router
from backend.routers.payout_audit import router as payout_audit_router

logger = logging.getLogger(__name__)

#: Paths that serve no tenant data and therefore need no tenant bound.
#: Everything else is scoped — the list is an exemption list, not an allowlist,
#: so a router added later is tenant-scoped by default rather than silently
#: unscoped until someone remembers to register it. The previous allowlist
#: omitted /agent, /workflows, /etl and /grading, and the agent consequently
#: reported revenue summed across every company in the database.
TENANT_EXEMPT_PREFIXES = (
    "/health",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/favicon.ico",
)


def is_tenant_exempt(path: str) -> bool:
    """True when `path` serves no tenant data."""
    if path == "/":
        return True
    return path.startswith(TENANT_EXEMPT_PREFIXES)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Refuse to boot into production mode without a way to verify tokens.
    # Failing at startup is the only safe option: the alternative is a server
    # that accepts every request because it cannot check any of them.
    assert_auth_configured()

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
async def tenant_binding_middleware(request: Request, call_next):
    """
    Bind the request's company for the duration of the request.

    Everything downstream — every ORM select and insert — is scoped to this value
    by backend/tenant_guard.py, so this is the single place a request acquires a
    tenant. It replaces the company-alignment middleware, which resolved the same
    value and then *reloaded the database* when it differed from a process-global
    active company. That is what made an unauthenticated
    `GET /analytics/kpis?company_id=other` drop and rebuild every table, and it is
    why only one tenant could be served at a time.

    Every path is scoped unless explicitly exempt (see TENANT_EXEMPT_PREFIXES).

    Resolution is deliberately not done here: `auth/tenant.py` owns it, because
    the choice of company is an authorization decision (a request hint may only
    confirm the caller's own company, never replace it) and belongs with the code
    that knows the caller's identity.
    """
    if is_tenant_exempt(request.url.path):
        return await call_next(request)

    company = extract_company_hint(request) or (
        settings.DEMO_DEFAULT_COMPANY if settings.DEMO_MODE else None
    )
    if not company:
        return await call_next(request)  # route dependencies will reject it

    with tenant_scope(company):
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
