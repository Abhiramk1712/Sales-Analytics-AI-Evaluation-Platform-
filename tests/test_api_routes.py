from fastapi import FastAPI
from fastapi.testclient import TestClient
from types import SimpleNamespace

from backend.routers import analytics as analytics_router
from backend.routers import agent as agent_router
from backend.routers import grading as grading_router
from backend.routers import ingestion as ingestion_router
from backend.routers import reports as reports_router


def test_reports_types_route():
    app = FastAPI()
    app.include_router(reports_router.router)

    client = TestClient(app)
    res = client.get("/reports/types")

    assert res.status_code == 200
    payload = res.json()
    assert "report_types" in payload
    assert "executive_weekly" in payload["report_types"]


def test_reports_generate_route_with_monkeypatched_generator(monkeypatch):
    app = FastAPI()
    app.include_router(reports_router.router)

    async def fake_generate_report(db, report_type, period, audience, filters):
        return {
            "report_type": report_type,
            "period": period,
            "audience": audience,
            "markdown": "# Stub Report",
            "metrics_used": ["total_revenue"],
            "generated_at": "2026-04-28T00:00:00Z",
            "warnings": [],
        }

    monkeypatch.setattr(reports_router.ReportGenerator, "generate_report", fake_generate_report)

    async def fake_db():
        yield object()

    app.dependency_overrides[reports_router.get_db] = fake_db
    client = TestClient(app)

    res = client.post(
        "/reports/generate",
        json={
            "report_type": "executive_weekly",
            "period": "2026-04",
            "audience": "CEO",
            "filters": {},
        },
    )

    assert res.status_code == 200
    payload = res.json()
    assert payload["markdown"].startswith("# Stub Report")
    assert payload["metrics_used"] == ["total_revenue"]


def test_reports_knowledge_base_routes():
    app = FastAPI()
    app.include_router(reports_router.router)
    client = TestClient(app)

    list_res = client.get("/reports/knowledge-base")
    assert list_res.status_code == 200
    docs = list_res.json().get("documents", [])
    assert isinstance(docs, list)

    if docs:
        doc_res = client.get(f"/reports/knowledge-base/{docs[0]}")
        assert doc_res.status_code == 200
        payload = doc_res.json()
        assert payload["document_name"].endswith(".md")
        assert isinstance(payload["content"], str)


def test_reports_knowledge_base_invalid_document_name():
    app = FastAPI()
    app.include_router(reports_router.router)
    client = TestClient(app)

    res = client.get("/reports/knowledge-base/../../etc/passwd")
    assert res.status_code == 404


def test_grading_route():
    app = FastAPI()
    app.include_router(grading_router.router)

    client = TestClient(app)
    res = client.get("/grading/enterprise-readiness")

    assert res.status_code == 200
    payload = res.json()
    assert "overall_score" in payload
    assert payload["grade"] in {"A", "B", "C", "D", "F"}


def test_agent_chat_route_structured_response(monkeypatch):
    app = FastAPI()
    app.include_router(agent_router.router)

    class StubPlanner:
        def plan(self, message):
            class State:
                user_message = message
                intent = "metric_question"
                tools_called = []
                evidence = {}
                evidence_results = []
                warnings = []
            return State()

    class StubExecutor:
        async def execute_for_intent(self, state, db_session=None):
            state.tools_called = ["get_sales_kpis"]
            state.evidence_results = [
                {
                    "tool_name": "get_sales_kpis",
                    "status": "success",
                    "data": {"total_revenue": 1000},
                    "warnings": [],
                    "sources": ["revenue"],
                }
            ]
            return state

    class StubVerifier:
        def verify_state(self, state):
            return True, []

    class StubLLM:
        async def chat_completion(self, **kwargs):
            return "Grounded response"

    monkeypatch.setattr(agent_router, "planner", StubPlanner())
    monkeypatch.setattr(agent_router, "executor", StubExecutor())
    monkeypatch.setattr(agent_router, "verifier", StubVerifier())
    monkeypatch.setattr(agent_router, "get_llm_provider", lambda **kwargs: StubLLM())

    async def fake_db():
        yield object()

    app.dependency_overrides[agent_router.get_db] = fake_db

    client = TestClient(app)
    res = client.post("/agent/chat", json={"message": "what is revenue?", "history": []})

    assert res.status_code == 200
    payload = res.json()
    assert payload["reply"] == "Grounded response"
    assert payload["intent"] == "metric_question"
    assert payload["tools_used"] == ["get_sales_kpis"]
    assert "evidence_summary" in payload
    assert "warnings" in payload
    assert "answer_quality" in payload
    assert 0 <= payload["answer_quality"]["score"] <= 100


def test_ingestion_inspect_route(monkeypatch):
    app = FastAPI()
    app.include_router(ingestion_router.router)

    def fake_inspect(path):
        return {"source_dir": path, "files": [{"entity": "deals"}], "warnings": []}

    monkeypatch.setattr(ingestion_router, "inspect_source_directory", fake_inspect)

    client = TestClient(app)
    res = client.post("/ingestion/inspect", json={"source_dir": "/tmp/import", "company_name": "Acme", "reset_database": True})

    assert res.status_code == 200
    assert res.json()["files"][0]["entity"] == "deals"


def test_ingestion_intelligent_load_route(monkeypatch):
    app = FastAPI()
    app.include_router(ingestion_router.router)

    async def fake_ingest(
        source_dir,
        company_name,
        reset_database=True,
        load_mode="full_reload",
        use_manifest=True,
        manifest_name="sales_schema",
        manifest_version="v1",
    ):
        return {
            "company_name": company_name,
            "company_dir": "companies/acme",
            "inspection": {"files": []},
            "quality_gate": {"confidence": 1.0, "overall_status": "ok", "blocked": False, "issues": [], "data_warnings": []},
            "load_mode": load_mode,
            "warnings": [],
            "db_rows_loaded": {"deals": 5},
            "audit_file": "companies/acme/ingestion_run_summary.json",
            "inferred_entities": ["deals"],
        }

    monkeypatch.setattr(ingestion_router, "intelligent_ingest", fake_ingest)

    client = TestClient(app)
    res = client.post("/ingestion/intelligent-load", json={"source_dir": "/tmp/import", "company_name": "Acme", "reset_database": True})

    assert res.status_code == 200
    payload = res.json()
    assert payload["company_name"] == "Acme"
    assert payload["db_rows_loaded"]["deals"] == 5


def test_ingestion_upload_intelligent_load_route(monkeypatch, tmp_path):
    app = FastAPI()
    app.include_router(ingestion_router.router)

    def fake_write_uploaded_sources(files):
        for file in files:
            _ = file.file.read()
        return tmp_path

    async def fake_ingest(
        source_dir,
        company_name,
        reset_database=True,
        load_mode="full_reload",
        use_manifest=True,
        manifest_name="sales_schema",
        manifest_version="v1",
    ):
        return {
            "company_name": company_name,
            "company_dir": "companies/acme",
            "inspection": {"files": [{"entity": "revenue"}]},
            "source_manifest": [{"source_type": "csv"}],
            "quality_gate": {"confidence": 1.0, "overall_status": "ok", "blocked": False, "issues": [], "data_warnings": []},
            "load_mode": load_mode,
            "warnings": [],
            "db_rows_loaded": {"revenue": 5},
            "audit_file": "companies/acme/ingestion_run_summary.json",
            "inferred_entities": ["revenue"],
        }

    monkeypatch.setattr(ingestion_router, "_write_uploaded_sources", fake_write_uploaded_sources)
    monkeypatch.setattr(ingestion_router, "intelligent_ingest", fake_ingest)

    client = TestClient(app)
    res = client.post(
        "/ingestion/upload-intelligent-load",
        data={"company_name": "Acme", "reset_database": "true"},
        files={"files": ("revenue.csv", "rep_id,period,amount\nabc,2026-04,1000\n", "text/csv")},
    )

    assert res.status_code == 200
    payload = res.json()
    assert payload["company_name"] == "Acme"
    assert payload["db_rows_loaded"]["revenue"] == 5
    assert payload["uploaded_files"] == ["revenue.csv"]


def test_ingestion_load_company_route(monkeypatch):
    app = FastAPI()
    app.include_router(ingestion_router.router)

    async def fake_load_company_dataset(company_name):
        return {"teams": 5, "reps": 2, "deals": 8}

    monkeypatch.setattr(ingestion_router, "load_company_dataset", fake_load_company_dataset)

    client = TestClient(app)
    res = client.post("/ingestion/load-company", json={"company_name": "Acme Corp"})

    assert res.status_code == 200
    payload = res.json()
    assert payload["company_name"] == "Acme Corp"
    assert payload["db_rows_loaded"]["reps"] == 2


def test_ingestion_companies_route():
    app = FastAPI()
    app.include_router(ingestion_router.router)

    client = TestClient(app)
    res = client.get("/ingestion/companies")

    assert res.status_code == 200
    payload = res.json()
    assert "companies" in payload
    assert isinstance(payload["companies"], list)


def test_analytics_org_structure_route(monkeypatch):
    app = FastAPI()
    app.include_router(analytics_router.router)

    class _FakeResult:
        def __init__(self, rows):
            self._rows = rows

        def scalars(self):
            return self

        def all(self):
            return self._rows

    class _FakeDB:
        def __init__(self):
            self.calls = 0

        async def execute(self, query):
            self.calls += 1
            if self.calls == 1:  # teams
                return _FakeResult([SimpleNamespace(id="T1", name="West Team", region="West")])
            if self.calls == 2:  # reps
                return _FakeResult([SimpleNamespace(id="R1", team_id="T1", name="Alice Rep", email="alice@example.com", region="West")])
            if self.calls == 3:  # users
                return _FakeResult([SimpleNamespace(id="U1", email="alice@example.com", team_id="T1")])
            if self.calls == 4:  # user territory assignments
                return _FakeResult([SimpleNamespace(user_id="U1", territory_id="TR1", is_primary=True)])
            if self.calls == 5:  # territories
                return _FakeResult([SimpleNamespace(id="TR1", name="North America", region="North America")])
            if self.calls == 6:  # revenue aggregate
                return _FakeResult([("R1", 100000.0)])
            if self.calls == 7:  # quota aggregate
                return _FakeResult([("R1", 120000.0)])
            if self.calls == 8:  # closed won aggregate
                return _FakeResult([("R1", 3)])
            if self.calls == 9:  # closed lost aggregate
                return _FakeResult([("R1", 1)])
            return _FakeResult([])

    async def fake_db():
        yield _FakeDB()

    app.dependency_overrides[analytics_router.get_db] = fake_db

    client = TestClient(app)
    res = client.get("/analytics/org-structure")

    assert res.status_code == 200
    payload = res.json()
    assert payload["data_available"] is True
    assert payload["summary"]["member_count"] == 1
    assert payload["territories"][0]["territory"] == "North America"
    assert payload["territories"][0]["teams"][0]["members"][0]["name"] == "Alice Rep"


def test_analytics_plans_governance_route(monkeypatch):
    app = FastAPI()
    app.include_router(analytics_router.router)

    class _FakeResult:
        def __init__(self, rows):
            self._rows = rows

        def scalars(self):
            return self

        def all(self):
            return self._rows

    class _FakeDB:
        def __init__(self):
            self.calls = 0

        async def execute(self, query):
            self.calls += 1
            if self.calls == 1:  # plans
                return _FakeResult([
                    SimpleNamespace(id="P1", external_id="PLAN-001", name="FY26 Enterprise", description="Enterprise plan", effective_start_date=None, effective_end_date=None),
                ])
            if self.calls == 2:  # rules
                return _FakeResult([
                    SimpleNamespace(id="R1", plan_id="P1", name="Tier 1", metric_name="attainment_pct", threshold_min=0, threshold_max=99.99, rate=0.05, bonus_amount=0),
                    SimpleNamespace(id="R2", plan_id="P1", name="Tier 2", metric_name="attainment_pct", threshold_min=100, threshold_max=999, rate=0.1, bonus_amount=1000),
                ])
            if self.calls == 3:  # assignments
                return _FakeResult([
                    SimpleNamespace(id="PA1", plan_id="P1", user_id="U1"),
                ])
            if self.calls == 4:  # users
                return _FakeResult([
                    SimpleNamespace(id="U1", name="Alice", email="alice@example.com", team_id="T1"),
                    SimpleNamespace(id="U2", name="Bob", email="bob@example.com", team_id="T1"),
                ])
            if self.calls == 5:  # teams
                return _FakeResult([
                    SimpleNamespace(id="T1", name="West Team"),
                ])
            return _FakeResult([])

    async def fake_db():
        yield _FakeDB()

    app.dependency_overrides[analytics_router.get_db] = fake_db

    client = TestClient(app)
    res = client.get("/analytics/plans-governance")

    assert res.status_code == 200
    payload = res.json()
    assert payload["data_available"] is True
    assert payload["summary"]["plan_count"] == 1
    assert payload["summary"]["rule_count"] == 2
    assert payload["summary"]["assignment_coverage_pct"] == 50.0
    assert payload["plans"][0]["assigned_user_count"] == 1
    assert payload["plans"][0]["rule_count"] == 2


def test_agent_chat_uses_deterministic_fallback_when_llm_unavailable(monkeypatch):
    app = FastAPI()
    app.include_router(agent_router.router)

    class StubPlanner:
        def plan(self, message):
            class State:
                user_message = message
                intent = "metric_question"
                tools_called = []
                evidence = {}
                evidence_results = []
                warnings = []
            return State()

    class StubExecutor:
        async def execute_for_intent(self, state, db_session=None):
            state.tools_called = ["get_sales_kpis"]
            state.evidence_results = [
                {
                    "tool_name": "get_sales_kpis",
                    "status": "success",
                    "data": {"total_revenue": 1500000, "attainment_pct": 92.4, "pipeline_coverage": 1.31},
                    "warnings": [],
                    "sources": ["revenue", "quotas"],
                }
            ]
            return state

    class StubVerifier:
        def verify_state(self, state):
            return True, []

    def broken_provider(**kwargs):
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr(agent_router, "planner", StubPlanner())
    monkeypatch.setattr(agent_router, "executor", StubExecutor())
    monkeypatch.setattr(agent_router, "verifier", StubVerifier())
    monkeypatch.setattr(agent_router, "get_llm_provider", broken_provider)

    async def fake_db():
        yield object()

    app.dependency_overrides[agent_router.get_db] = fake_db

    client = TestClient(app)
    res = client.post("/agent/chat", json={"message": "what is our revenue", "history": []})
    assert res.status_code == 200
    payload = res.json()
    assert "deterministic evidence-backed summary" in payload["reply"]
    assert "LLM provider unavailable" in " ".join(payload["warnings"])


def test_agent_ml_evidence_snapshot_route(monkeypatch):
    app = FastAPI()
    app.include_router(agent_router.router)

    async def fake_forecast(_db):
        return {
            "tool_name": "get_forecast_summary",
            "status": "success",
            "data": {"latest_model_version": "v1", "predicted_at": "2026-01-01T00:00:00Z"},
            "warnings": [],
            "sources": ["ml_predictions"],
        }

    async def fake_deal_risk(_db):
        return {
            "tool_name": "get_deal_risk_summary",
            "status": "success",
            "data": {"high_risk_count": 2, "medium_risk_count": 4, "low_risk_count": 10},
            "warnings": [],
            "sources": ["ml_predictions"],
        }

    async def fake_clusters(_db):
        return {
            "tool_name": "get_rep_clusters_summary",
            "status": "warning",
            "data": {"clusters": [{"name": "Top Performer", "rep_count": 3}]},
            "warnings": ["stale clustering run"],
            "sources": ["ml_predictions"],
        }

    monkeypatch.setattr(agent_router, "get_forecast_summary", fake_forecast)
    monkeypatch.setattr(agent_router, "get_deal_risk_summary", fake_deal_risk)
    monkeypatch.setattr(agent_router, "get_rep_clusters_summary", fake_clusters)

    async def fake_db():
        yield object()

    app.dependency_overrides[agent_router.get_db] = fake_db
    client = TestClient(app)

    res = client.get("/agent/ml-evidence")
    assert res.status_code == 200
    body = res.json()
    assert body["forecast"]["latest_model_version"] == "v1"
    assert body["deal_risk"]["high_risk_count"] == 2
    assert body["rep_clusters"]["cluster_count"] == 1
    assert "stale clustering run" in body["warnings"]
