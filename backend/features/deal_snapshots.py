"""
backend/features/deal_snapshots.py
====================================
Deal snapshot builder for leakage-safe deal scoring.

PROBLEM
-------
Training deal-scoring models on the final deal row risks data leakage because
the row may already contain post-outcome fields (actual_close_date, final stage,
closed_reason, etc.) that encode the outcome you're trying to predict.

SOLUTION
--------
Build "pre-close snapshots" — feature rows that represent what was *knowable*
at a point in time *before* the deal closed.

If true historical stage-history tables don't exist (they don't in this schema),
we build ONE safe pre-close snapshot per deal using only the fields available
at deal-creation time plus safe derived fields.

LEAKAGE-SAFE FEATURE POLICY
-----------------------------
  ALLOWED (safe at any pre-close snapshot):
    amount, stage (non-terminal only), days_in_pipeline,
    activity_count_14d, days_since_last_activity,
    days_until_expected_close, account_segment, rep_id, product,
    team_id, industry, month_created

  EXPLICITLY EXCLUDED (outcome-leaking):
    actual_close_date    — only known after close
    stage == 'Closed Won' or 'Closed Lost'  — the outcome itself
    close_probability    — may be set by rep AFTER knowing result
    any field updated_at close time

WARNING
-------
Without a proper stage-history table the snapshots are approximations.
All predictions carry a warning label:
  "Historical stage snapshots unavailable; using derived pre-close snapshot approximation."
"""
from __future__ import annotations

from datetime import datetime, date, timedelta
from typing import Any, Optional
import uuid

import pandas as pd

# ── Constants ──────────────────────────────────────────────────────────────

_TERMINAL_STAGES = {"Closed Won", "Closed Lost"}
_STAGE_ORDER = ["Prospecting", "Qualification", "Proposal", "Negotiation"]

ALLOWED_FEATURES: list[str] = [
    "amount",
    "stage",                       # non-terminal only
    "days_in_pipeline",
    "activity_count",
    "days_since_last_activity",
    "days_until_expected_close",
    "product",
    "industry",
    "month_created",
    # NLP-derived activity-note features (safe pre-close behavioral signals)
    "notes_sentiment_score",
    "notes_urgency_score",
    "notes_followup_signal",
    "notes_avg_length",
    "notes_token_count",
    "notes_positive_keyword_hits",
    "notes_negative_keyword_hits",
]

EXCLUDED_FEATURES: list[str] = [
    "actual_close_date",
    "close_probability",           # may encode outcome if set at close
    "stage_Closed_Won",
    "stage_Closed_Lost",
    "final_stage",
    "closed_at",
]

SNAPSHOT_WARNING = (
    "[FALLBACK] Historical stage snapshots unavailable; "
    "using derived pre-close snapshot approximation. "
    "Predictions carry uncertainty — treat as directional, not precise."
)


# ── Snapshot builder ───────────────────────────────────────────────────────

def build_deal_snapshots(
    deals: list[dict[str, Any]],
    activities: Optional[list[dict[str, Any]]] = None,
    snapshot_date: Optional[date] = None,
) -> tuple[pd.DataFrame, list[str]]:
    """
    Build pre-close deal snapshot rows from a list of deal dicts.

    Parameters
    ----------
    deals : list[dict]
        Each dict must contain at minimum:
          id, amount, stage, created_at, expected_close_date
        Optional: account_id, rep_id, product, industry
    activities : list[dict], optional
        Each dict: {deal_id, activity_date, ...}
        Used to compute activity_count and recency features.
    snapshot_date : date, optional
        Reference date for computing "days_until_expected_close".
        Defaults to today.

    Returns
    -------
    (DataFrame of snapshot rows, list of warning messages)

    Notes
    -----
    Only non-terminal-stage deals are included.
    Terminal stage deals (Closed Won / Lost) are included for label generation
    but their stage is masked as the last pre-terminal stage.
    """
    ref_date = snapshot_date or date.today()
    warnings: list[str] = [SNAPSHOT_WARNING]

    if not deals:
        return pd.DataFrame(), warnings

    # Index activities by deal_id
    act_by_deal: dict[str, list[date]] = {}
    for act in (activities or []):
        did = str(act.get("deal_id", ""))
        adate = act.get("activity_date")
        if adate:
            if isinstance(adate, datetime):
                adate = adate.date()
            elif isinstance(adate, str):
                try:
                    adate = datetime.strptime(adate[:10], "%Y-%m-%d").date()
                except ValueError:
                    continue
            act_by_deal.setdefault(did, []).append(adate)

    rows: list[dict[str, Any]] = []
    for deal in deals:
        deal_id = str(deal.get("id", uuid.uuid4()))
        stage = str(deal.get("stage", "Prospecting"))
        is_terminal = stage in _TERMINAL_STAGES

        # Mask terminal stage to last safe stage
        safe_stage = _STAGE_ORDER[-1] if is_terminal else stage
        # Only score non-terminal deals as open; terminal ones get final_outcome label
        final_outcome: Optional[int] = None
        if is_terminal:
            final_outcome = 1 if stage == "Closed Won" else 0

        # created_at
        created_at = deal.get("created_at")
        if isinstance(created_at, str):
            try:
                created_at = datetime.strptime(created_at[:10], "%Y-%m-%d").date()
            except ValueError:
                created_at = ref_date
        elif isinstance(created_at, datetime):
            created_at = created_at.date()
        elif created_at is None:
            created_at = ref_date

        days_in_pipeline = (ref_date - created_at).days if created_at <= ref_date else 0

        # Expected close date
        exp_close = deal.get("expected_close_date")
        if isinstance(exp_close, str):
            try:
                exp_close = datetime.strptime(exp_close[:10], "%Y-%m-%d").date()
            except ValueError:
                exp_close = None
        elif isinstance(exp_close, datetime):
            exp_close = exp_close.date()
        days_until_close = (exp_close - ref_date).days if exp_close else 90  # default 90 days

        # Activity features
        deal_acts = act_by_deal.get(deal_id, [])
        if deal_acts:
            activity_count = len(deal_acts)
            cutoff_14d = ref_date - timedelta(days=14)
            activity_count_14d = sum(1 for d in deal_acts if d >= cutoff_14d)
            latest_act = max(deal_acts)
            days_since_last_activity = (ref_date - latest_act).days
        else:
            # Fall back to pre-aggregated values in the deal dict when no activity events given
            activity_count = int(deal.get("activity_count") or 0)
            activity_count_14d = int(deal.get("activity_count_14d") or 0)
            raw_dsla = deal.get("days_since_last_activity")
            days_since_last_activity = int(raw_dsla) if raw_dsla is not None else days_in_pipeline

        rows.append({
            "deal_id":                   deal_id,
            "snapshot_date":             ref_date.isoformat(),
            # Safe features
            "amount":                    float(deal.get("amount", 0) or 0),
            "stage":                     safe_stage,
            "days_in_pipeline":          days_in_pipeline,
            "activity_count":            activity_count,
            "activity_count_14d":        activity_count_14d,
            "days_since_last_activity":  days_since_last_activity,
            "days_until_expected_close": days_until_close,
            "product":                   str(deal.get("product") or "Unknown"),
            "industry":                  str(deal.get("industry") or "Unknown"),
            "month_created":             created_at.month,
            # Labels (None for open deals; used only in training pipeline)
            "final_outcome":             final_outcome,
            "is_terminal":               is_terminal,
            # Metadata (not used as features)
            "rep_id":                    str(deal.get("rep_id") or ""),
            "account_id":                str(deal.get("account_id") or ""),
        })

    df = pd.DataFrame(rows)
    return df, warnings


def get_allowed_deal_features() -> list[str]:
    """Return the leakage-safe feature list for deal scoring."""
    return list(ALLOWED_FEATURES)


def get_excluded_features() -> list[str]:
    """Return fields explicitly excluded to prevent label leakage."""
    return list(EXCLUDED_FEATURES)


def assert_no_leakage(df: pd.DataFrame) -> list[str]:
    """
    Check a feature DataFrame for any leakage fields.

    Returns a list of leakage violations found. Empty list = safe.
    """
    violations = []
    for col in df.columns:
        if col in EXCLUDED_FEATURES or col.lower() in {f.lower() for f in EXCLUDED_FEATURES}:
            violations.append(f"Leakage field detected in feature set: '{col}'")
    return violations
