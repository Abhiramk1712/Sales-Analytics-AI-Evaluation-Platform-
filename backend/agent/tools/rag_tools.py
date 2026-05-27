"""
RAG retrieval tools for agent knowledge grounding.
"""
from __future__ import annotations

from typing import Any

from backend.rag.rag_service import get_rag_service


def retrieve_knowledge_context(query: str, top_k: int = 5, allow_numeric_claims: bool = False) -> dict[str, Any]:
    if not query or not query.strip():
        return {
            "tool_name": "retrieve_knowledge_context",
            "status": "warning",
            "data": [],
            "warnings": ["Empty query provided to RAG retrieval"],
            "sources": ["docs/knowledge_base"],
        }

    service = get_rag_service()
    if hasattr(service, "retrieve_context_annotated"):
        chunks = service.retrieve_context_annotated(query=query, top_k=top_k)
    else:
        # Backward-compatible path for older stubs/services.
        chunks = service.retrieve_context(query=query, top_k=top_k)
        chunks = [{**chunk, "has_numeric_claims": False} for chunk in chunks]
    warnings: list[str] = []
    filtered_numeric_claims_count = 0

    if not allow_numeric_claims:
        filtered_numeric_claims_count = sum(1 for chunk in chunks if chunk.get("has_numeric_claims"))
        if filtered_numeric_claims_count:
            warnings.append(
                f"Filtered {filtered_numeric_claims_count} chunk(s) with unverified numeric claims. "
                "Use DB-backed analytics tools for live figures."
            )
        chunks = [chunk for chunk in chunks if not chunk.get("has_numeric_claims")]

    if not chunks:
        warnings.append("No safe knowledge base chunks matched this query")

    return {
        "tool_name": "retrieve_knowledge_context",
        "status": "warning" if warnings else "success",
        "data": chunks,
        "warnings": warnings,
        "filtered_numeric_claims_count": filtered_numeric_claims_count,
        "sources": sorted({chunk.get("source_document", "unknown") for chunk in chunks}) or ["docs/knowledge_base"],
    }
