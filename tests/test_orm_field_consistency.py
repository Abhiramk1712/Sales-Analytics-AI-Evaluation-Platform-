from __future__ import annotations

from pathlib import Path


BANNED_ORM_REFERENCES = {
    "Quota.quota_amount",
    "Revenue.date",
    "Rep.user_id",
    "Rule.rule_type",
    "Rule.metric_type",
    "Rule.threshold_low",
    "Rule.threshold_high",
    "Rule.payout_amount",
    "Rule.cap_amount",
    "Rule.accelerator_rate",
    "Deal.owner_rep_id",
    "Deal.close_date",
    "MLPrediction.model_type",
}


def test_no_banned_orm_references_in_backend_code():
    backend_root = Path("backend")
    offenders: list[str] = []

    for py_file in backend_root.rglob("*.py"):
        text = py_file.read_text(encoding="utf-8")
        for token in BANNED_ORM_REFERENCES:
            if token in text:
                offenders.append(f"{py_file}: {token}")

    assert not offenders, "Found invalid ORM references:\n" + "\n".join(offenders)
