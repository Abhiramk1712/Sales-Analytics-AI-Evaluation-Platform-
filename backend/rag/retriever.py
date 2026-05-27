"""
backend/rag/retriever.py
========================
Hybrid retriever: TF-IDF + alias expansion + heading/title boosting.
"""
from __future__ import annotations

import re
from typing import Dict, List, Any, Optional

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer


# ── Alias dictionary ────────────────────────────────────────────────────────
# Maps canonical term → list of synonyms to inject into the query.
QUERY_ALIASES: Dict[str, List[str]] = {
    "quota attainment": ["attainment", "quota achieved", "target achievement", "percent to quota", "pct to quota", "% to quota"],
    "attainment": ["quota attainment", "quota achieved", "target achievement"],
    "pipeline coverage": ["coverage ratio", "pipeline to quota", "3x pipeline", "pipe coverage"],
    "pipeline": ["open pipeline", "active pipeline", "deals in pipeline", "pipeline value"],
    "payout": ["commission", "compensation", "incentive", "earnings", "sales pay"],
    "commission": ["payout", "compensation", "incentive", "earnings"],
    "forecast": ["projection", "prediction", "commit", "best case", "revenue forecast"],
    "nrr": ["net revenue retention", "net retention", "net dollar retention"],
    "grr": ["gross revenue retention", "gross retention", "gross dollar retention"],
    "arr": ["annual recurring revenue", "annual revenue run rate"],
    "churn": ["churn rate", "logo churn", "revenue churn", "customer churn"],
    "win rate": ["close rate", "conversion rate", "opportunity win rate"],
    "deal size": ["average deal size", "avg deal size", "deal value", "average contract value", "acv"],
    "sales cycle": ["average sales cycle", "time to close", "days to close"],
    "accelerator": ["kicker", "commission accelerator", "overachievement bonus", "above target"],
    "spiff": ["spot incentive", "special incentive", "bonus incentive"],
    "clawback": ["claw back", "charge back", "commission reversal"],
}

# Source document priority boosts (higher = surfaced earlier)
_SOURCE_BOOSTS: Dict[str, float] = {
    "metric_definitions.md": 0.3,
    "payout_methodology.md": 0.3,
    "revops_kpi_guide.md": 0.2,
    "forecasting_assumptions.md": 0.2,
    "quota_setting_methodology.md": 0.2,
    "sales_glossary.md": 0.15,
    "pipeline_inspection_guide.md": 0.1,
    "reporting_templates.md": 0.1,
}


def _expand_query(query: str) -> str:
    """Inject alias synonyms into the query string."""
    lower = query.lower()
    extra: List[str] = []
    for canonical, synonyms in QUERY_ALIASES.items():
        if canonical in lower:
            extra.extend(synonyms)
    if extra:
        return query + " " + " ".join(extra)
    return query


def _heading_score(chunk: Dict[str, Any], query_words: List[str]) -> float:
    """Bonus score for heading/title word overlap."""
    heading = (chunk.get("heading", "") or "").lower()
    if not heading:
        return 0.0
    return sum(0.2 for w in query_words if len(w) > 3 and w in heading)


def _source_boost(chunk: Dict[str, Any]) -> float:
    source = chunk.get("source_document", "") or chunk.get("source", "")
    for fname, boost in _SOURCE_BOOSTS.items():
        if fname in source:
            return boost
    return 0.0


class Retriever:
    """Hybrid TF-IDF retriever with alias expansion and source boosting."""

    def __init__(self, chunks: List[Dict[str, Any]]) -> None:
        self.chunks = chunks
        self._build_index()

    def _build_index(self) -> None:
        if not self.chunks:
            self.vectorizer = None
            self.tfidf_matrix = None
            return
        texts = [_expand_query(c.get("content", "")) for c in self.chunks]
        self.vectorizer = TfidfVectorizer(max_features=1000, stop_words="english", ngram_range=(1, 2))
        try:
            self.tfidf_matrix = self.vectorizer.fit_transform(texts)
        except Exception:
            self.vectorizer = None
            self.tfidf_matrix = None

    def retrieve(self, query: str, top_k: int = 5) -> List[Dict[str, Any]]:
        if not query or not query.strip():
            return []
        expanded = _expand_query(query)
        if self.vectorizer and self.tfidf_matrix is not None:
            try:
                query_vec = self.vectorizer.transform([expanded])
                raw_scores = query_vec.dot(self.tfidf_matrix.T).toarray()[0]
                query_words = query.lower().split()
                # Apply heading boost + source boost
                boosted = np.array([
                    raw_scores[i]
                    + _heading_score(self.chunks[i], query_words)
                    + _source_boost(self.chunks[i])
                    for i in range(len(self.chunks))
                ])
                top_indices = np.argsort(boosted)[-top_k:][::-1]
                results = []
                for idx in top_indices:
                    if boosted[idx] > 0:
                        r = self.chunks[idx].copy()
                        r["score"] = float(boosted[idx])
                        r["source_document"] = r.get("source_document") or r.get("source", "unknown")
                        results.append(r)
                return results
            except Exception:
                pass
        return self._keyword_match(expanded, top_k)

    def _keyword_match(self, query: str, top_k: int) -> List[Dict[str, Any]]:
        query_words = query.lower().split()
        scores = []
        for chunk in self.chunks:
            content = (chunk.get("content", "") or "").lower()
            score = sum(1 for w in query_words if w in content)
            score += _heading_score(chunk, query_words)
            score += _source_boost(chunk)
            scores.append(score)
        top_indices = np.argsort(scores)[-top_k:][::-1]
        results = []
        for idx in top_indices:
            if scores[idx] > 0:
                r = self.chunks[idx].copy()
                r["score"] = float(scores[idx])
                r["source_document"] = r.get("source_document") or r.get("source", "unknown")
                results.append(r)
        return results

