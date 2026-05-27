from backend.rag.rag_service import RAGService
from backend.agent.tools.rag_tools import retrieve_knowledge_context


def test_rag_service_retrieve_returns_expected_fields():
    service = RAGService("docs/knowledge_base")
    rows = service.retrieve_context("quota attainment", top_k=3)
    assert isinstance(rows, list)
    if rows:
        item = rows[0]
        assert "content" in item
        assert "source_document" in item
        assert "score" in item


def test_rag_tool_handles_empty_query_safely():
    result = retrieve_knowledge_context("", top_k=3)
    assert result["status"] == "warning"
    assert result["data"] == []


def test_rag_tool_filters_unverified_numeric_claims(monkeypatch):
    class _StubService:
        def retrieve_context_annotated(self, query: str, top_k: int = 5):
            return [
                {
                    "content": "Definition: quota attainment is revenue divided by quota.",
                    "source_document": "metric_definitions.md",
                    "score": 0.9,
                    "has_numeric_claims": False,
                },
                {
                    "content": "[UNVERIFIED] Revenue is $12,345,678 this month.",
                    "source_document": "forecasting_assumptions.md",
                    "score": 0.8,
                    "has_numeric_claims": True,
                },
            ]

    monkeypatch.setattr("backend.agent.tools.rag_tools.get_rag_service", lambda: _StubService())

    result = retrieve_knowledge_context("what is quota attainment", top_k=3)
    assert result["status"] == "warning"
    assert result["filtered_numeric_claims_count"] == 1
    assert len(result["data"]) == 1
    assert result["data"][0]["source_document"] == "metric_definitions.md"


def test_rag_tool_can_include_numeric_claims_when_explicitly_allowed(monkeypatch):
    class _StubService:
        def retrieve_context_annotated(self, query: str, top_k: int = 5):
            return [
                {
                    "content": "[UNVERIFIED] Revenue is $12,345,678 this month.",
                    "source_document": "forecasting_assumptions.md",
                    "score": 0.8,
                    "has_numeric_claims": True,
                },
            ]

    monkeypatch.setattr("backend.agent.tools.rag_tools.get_rag_service", lambda: _StubService())

    result = retrieve_knowledge_context("give me that number", top_k=3, allow_numeric_claims=True)
    assert result["filtered_numeric_claims_count"] == 0
    assert result["data"]
