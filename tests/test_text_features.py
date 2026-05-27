from backend.ml.text_features import (
    TEXT_FEATURE_COLUMNS,
    build_deal_text_feature_frame,
    extract_activity_note_features,
    vectorize_notes_tfidf,
)


def test_extract_activity_note_features_empty():
    result = extract_activity_note_features([])
    assert all(col in result for col in TEXT_FEATURE_COLUMNS)
    assert result["notes_token_count"] == 0.0


def test_extract_activity_note_features_non_empty():
    notes = [
        "Customer approved budget and requested next step ASAP.",
        "Slight concern from security team but moving forward.",
    ]
    result = extract_activity_note_features(notes)
    assert result["notes_token_count"] > 0
    assert result["notes_urgency_score"] >= 0


def test_build_deal_text_feature_frame_groups_by_deal():
    activities = [
        {"deal_id": "d1", "notes": "Urgent follow up call"},
        {"deal_id": "d1", "notes": "Customer approved proposal"},
        {"deal_id": "d2", "notes": "No response from stakeholder"},
    ]
    df = build_deal_text_feature_frame(activities)
    assert set(df["deal_id"].tolist()) == {"d1", "d2"}
    assert all(col in df.columns for col in TEXT_FEATURE_COLUMNS)


def test_vectorize_notes_tfidf_shape():
    matrix, names = vectorize_notes_tfidf(["great progress", "blocked due to budget"], max_features=8)
    assert matrix.shape[0] == 2
    assert len(names) <= 8
