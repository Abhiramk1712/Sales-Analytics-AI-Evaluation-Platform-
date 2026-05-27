"""
backend/rag/rag_service.py
==========================
RAG service for retrieving and using knowledge base
"""
import re
from typing import List, Dict, Any, Optional
from backend.rag.document_loader import DocumentLoader
from backend.rag.chunker import DocumentChunker
from backend.rag.retriever import Retriever


class RAGService:
    """
    Retrieval-Augmented Generation service.
    Loads documents, chunks them, and retrieves relevant information.
    """
    
    def __init__(self, knowledge_base_dir: str = "docs/knowledge_base"):
        """Initialize RAG service."""
        self.loader = DocumentLoader(knowledge_base_dir)
        self.chunker = DocumentChunker()

        self._kb_signature: tuple | None = None
        self.retriever = Retriever([])
        self._reload_documents()

    def _compute_kb_signature(self) -> tuple:
        kb_dir = self.loader.kb_dir
        if not kb_dir.exists():
            return tuple()
        sig: list[tuple[str, int]] = []
        for md_file in sorted(kb_dir.glob("*.md")):
            sig.append((md_file.name, md_file.stat().st_mtime_ns))
        return tuple(sig)

    def _reload_documents(self) -> None:
        documents = self.loader.load_all()
        chunks = self.chunker.chunk(documents) if documents else []
        self.retriever = Retriever(chunks)
        self._kb_signature = self._compute_kb_signature()

    def _refresh_if_needed(self) -> None:
        new_sig = self._compute_kb_signature()
        if new_sig != self._kb_signature:
            self._reload_documents()
    
    def retrieve_context(self, query: str, top_k: int = 5) -> List[Dict[str, Any]]:
        """
        Retrieve relevant chunks from knowledge base.
        
        Args:
            query: User query
            top_k: Number of chunks to return
        
        Returns:
            List of relevant chunks with sources
        """
        self._refresh_if_needed()
        chunks = self.retriever.retrieve(query, top_k)
        normalized = []
        for chunk in chunks:
            normalized.append(
                {
                    "content": chunk.get("content", ""),
                    "source_document": chunk.get("source_document") or chunk.get("source", "unknown"),
                    "score": chunk.get("score", 0.0),
                    "heading": chunk.get("heading", ""),
                }
            )
        return normalized

    def retrieve_context_annotated(self, query: str, top_k: int = 5) -> List[Dict[str, Any]]:
        """
        Same as retrieve_context but with numeric-claim annotation applied.
        Chunks containing unverified numeric figures are flagged with
        ``has_numeric_claims=True`` and prefixed with an [UNVERIFIED] warning.
        """
        chunks = self.retrieve_context(query, top_k)
        return annotate_chunks_for_numeric_content(chunks)

    def format_retrieved_context(self, chunks: List[Dict[str, Any]]) -> str:
        """Format retrieved chunks into readable text."""
        if not chunks:
            return ""
        
        formatted = []
        for chunk in chunks:
            source = chunk.get("source_document", "unknown")
            heading = chunk.get("heading", "")
            content = chunk.get("content", "")
            
            if heading:
                formatted.append(f"From {source} ({heading}):\n{content}")
            else:
                formatted.append(f"From {source}:\n{content}")
        
        return "\n\n".join(formatted)


# Global RAG service instance
_global_rag_service: Optional[RAGService] = None


def get_rag_service() -> RAGService:
    """Get or create global RAG service."""
    global _global_rag_service
    if _global_rag_service is None:
        _global_rag_service = RAGService()
    return _global_rag_service


# ---------------------------------------------------------------------------
# Numeric boundary enforcement
# ---------------------------------------------------------------------------

# Patterns that suggest a RAG chunk contains specific company data figures.
# Matches dollar amounts like $1,234,567 or $1.2M, percentages with precision
# like 73.4%, or plain numbers with 5+ digits (revenue-scale figures).
_NUMERIC_PATTERN = re.compile(
    r'(\$[\d,]+(?:\.\d+)?[KMB]?|\b\d{5,}\b|\b\d{1,3}(?:,\d{3})+(?:\.\d+)?\b|\b\d{1,3}\.\d{1,2}%)'
)

# Phrases that indicate the number is a benchmark/definition (safe to surface)
_BENCHMARK_INDICATORS = re.compile(
    r'\b(benchmark|target|healthy|typical|industry|≥|≤|at least|at most|goal|standard|should be)\b',
    re.IGNORECASE,
)


def has_unverified_numeric_claims(chunk_content: str) -> bool:
    """
    Return True if a RAG chunk appears to contain specific numeric figures
    that are NOT benchmark/definition context — i.e., values that look like
    live company data that should come from the DB instead.
    """
    for match in _NUMERIC_PATTERN.finditer(chunk_content):
        # Check surrounding 120 chars for benchmark language
        start = max(0, match.start() - 60)
        end = min(len(chunk_content), match.end() + 60)
        surrounding = chunk_content[start:end]
        if not _BENCHMARK_INDICATORS.search(surrounding):
            return True
    return False


def annotate_chunks_for_numeric_content(chunks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Inspect each retrieved chunk and set ``has_numeric_claims=True`` plus
    prepend ``[UNVERIFIED — use DB tool for live data]`` to any chunk that
    appears to contain specific numeric figures outside of benchmarks.
    """
    result = []
    for chunk in chunks:
        content = chunk.get("content", "")
        flagged = has_unverified_numeric_claims(content)
        annotated = dict(chunk)
        annotated["has_numeric_claims"] = flagged
        if flagged:
            annotated["content"] = "[UNVERIFIED — verify with live data tools]\n" + content
        result.append(annotated)
    return result

