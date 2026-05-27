"""
backend/ml/text_features.py
===========================
Lightweight NLP feature extraction for deal activity notes.

Features are intentionally compact and leakage-safe:
- They are derived from text available before a deal closes.
- They avoid target leakage fields (no outcome labels in input).
"""
from __future__ import annotations

from collections import defaultdict
import re
from typing import Any

import numpy as np
import pandas as pd
from sklearn.feature_extraction.text import TfidfVectorizer


POSITIVE_KEYWORDS = {
    "approved", "agreed", "confirmed", "moving forward", "green light", "signed", "excited", "priority"
}
NEGATIVE_KEYWORDS = {
    "blocked", "stalled", "delayed", "budget issue", "no response", "risk", "concern", "escalation", "pushback"
}
URGENCY_KEYWORDS = {
    "urgent", "asap", "eod", "deadline", "this week", "today", "immediately", "priority"
}
FOLLOWUP_KEYWORDS = {
    "next step", "follow up", "follow-up", "meeting", "schedule", "demo", "proposal", "review"
}

_TEXT_TOKEN_PATTERN = re.compile(r"[a-zA-Z][a-zA-Z']+")

TEXT_FEATURE_COLUMNS = [
    "notes_sentiment_score",
    "notes_urgency_score",
    "notes_followup_signal",
    "notes_avg_length",
    "notes_token_count",
    "notes_positive_keyword_hits",
    "notes_negative_keyword_hits",
]


def _empty_text_features() -> dict[str, float]:
    return {
        "notes_sentiment_score": 0.0,
        "notes_urgency_score": 0.0,
        "notes_followup_signal": 0.0,
        "notes_avg_length": 0.0,
        "notes_token_count": 0.0,
        "notes_positive_keyword_hits": 0.0,
        "notes_negative_keyword_hits": 0.0,
    }


def _normalize_note(note: str) -> str:
    text = (note or "").strip().lower()
    return re.sub(r"\s+", " ", text)


def _count_phrase_hits(notes: list[str], keywords: set[str]) -> int:
    hits = 0
    for note in notes:
        for kw in keywords:
            if kw in note:
                hits += 1
    return hits


def extract_activity_note_features(activity_notes: list[str]) -> dict[str, float]:
    """Extract aggregate text features from a list of activity note strings."""
    clean_notes = [_normalize_note(n) for n in activity_notes if (n or "").strip()]
    if not clean_notes:
        return _empty_text_features()

    tokens = []
    for note in clean_notes:
        tokens.extend(_TEXT_TOKEN_PATTERN.findall(note))

    token_count = float(len(tokens))
    avg_len = float(np.mean([len(n) for n in clean_notes])) if clean_notes else 0.0

    pos_hits = _count_phrase_hits(clean_notes, POSITIVE_KEYWORDS)
    neg_hits = _count_phrase_hits(clean_notes, NEGATIVE_KEYWORDS)
    urgency_hits = _count_phrase_hits(clean_notes, URGENCY_KEYWORDS)
    followup_hits = _count_phrase_hits(clean_notes, FOLLOWUP_KEYWORDS)

    sentiment_score = float((pos_hits - neg_hits) / max(pos_hits + neg_hits, 1))
    urgency_score = float(urgency_hits / max(len(clean_notes), 1))
    followup_signal = float(followup_hits / max(len(clean_notes), 1))

    return {
        "notes_sentiment_score": round(sentiment_score, 4),
        "notes_urgency_score": round(urgency_score, 4),
        "notes_followup_signal": round(followup_signal, 4),
        "notes_avg_length": round(avg_len, 2),
        "notes_token_count": round(token_count, 2),
        "notes_positive_keyword_hits": float(pos_hits),
        "notes_negative_keyword_hits": float(neg_hits),
    }


def vectorize_notes_tfidf(notes_corpus: list[str], max_features: int = 100) -> tuple[np.ndarray, list[str]]:
    """Return dense TF-IDF matrix and feature names for optional diagnostics."""
    clean_notes = [(_normalize_note(t) or "") for t in notes_corpus]
    if not clean_notes or all(not t for t in clean_notes):
        return np.zeros((len(clean_notes), 0), dtype=float), []

    vectorizer = TfidfVectorizer(max_features=max_features, ngram_range=(1, 2), min_df=1)
    matrix = vectorizer.fit_transform(clean_notes)
    return matrix.toarray(), vectorizer.get_feature_names_out().tolist()


def build_deal_text_feature_frame(activities: list[dict[str, Any]]) -> pd.DataFrame:
    """
    Build per-deal text feature rows from activity events.

    Expected activity fields: deal_id, notes
    """
    by_deal: dict[str, list[str]] = defaultdict(list)
    for act in activities:
        deal_id = str(act.get("deal_id") or "").strip()
        if not deal_id:
            continue
        note = str(act.get("notes") or "").strip()
        if note:
            by_deal[deal_id].append(note)

    rows: list[dict[str, Any]] = []
    for deal_id, notes in by_deal.items():
        feature_row = extract_activity_note_features(notes)
        feature_row["deal_id"] = deal_id
        rows.append(feature_row)

    if not rows:
        return pd.DataFrame(columns=["deal_id", *TEXT_FEATURE_COLUMNS])

    df = pd.DataFrame(rows)
    for col in TEXT_FEATURE_COLUMNS:
        if col not in df.columns:
            df[col] = 0.0
    return df[["deal_id", *TEXT_FEATURE_COLUMNS]]
