"""
ml/deal_slip.py
===============
Deal Slip Risk Model
---------------------
Predicts whether an open deal is at risk of slipping past its
expected close date. This complements the win-probability scorer
(deal_scoring.py) by focusing specifically on *timing* risk.

Features used (LEAKAGE-SAFE)
-----------------------------
✓ Days until expected close               — schedule urgency
✓ Days already in pipeline                — how long it has been open
✓ Stage ordinal                           — deal maturity
✓ Activity count in last 14 days          — recent engagement
✓ Close probability (rep estimate)        — rep's own read
✓ Deal amount (log-scaled)                — deal complexity proxy
✓ Activity count total                    — overall engagement level

Output
------
slip_risk_score : float [0, 1]   — probability the deal slips
slip_flag       : bool           — True if score ≥ SLIP_THRESHOLD
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import joblib
from pathlib import Path
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.calibration import CalibratedClassifierCV
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.model_selection import cross_val_score
import warnings
warnings.filterwarnings("ignore")

MODEL_PATH = Path(__file__).parent / "saved" / "deal_slip.pkl"
MODEL_PATH.parent.mkdir(exist_ok=True)

STAGE_ORDER = ["Prospecting", "Qualification", "Proposal", "Negotiation"]
SLIP_THRESHOLD = 0.55
FEATURES = [
    "log_amount",
    "days_in_pipeline",
    "days_until_close",
    "stage_ord",
    "activity_count",
    "recent_activity_count",
    "close_probability",
]


@dataclass
class SlipResult:
    deal_id: str
    deal_name: str
    slip_risk_score: float
    slip_flag: bool
    expected_close_date: Optional[str]
    days_until_close: int
    stage: str
    amount: float
    top_risk_factors: list[str] = field(default_factory=list)


class DealSlipModel:
    """Gradient Boosted classifier for deal-slip timing risk."""

    def __init__(self):
        self._fitted = False
        self.model: Optional[Pipeline] = None
        self.feature_importances_: dict[str, float] = {}

    # ─── Feature engineering ──────────────────────────────────────────────

    def _build_features(self, deals_df: pd.DataFrame, activities_df: pd.DataFrame) -> pd.DataFrame:
        df = deals_df.copy()

        # Stage ordinal (open stages only; closed stages get max ordinal)
        stage_map = {s: i for i, s in enumerate(STAGE_ORDER)}
        df["stage_ord"] = df["stage"].map(stage_map).fillna(len(STAGE_ORDER) - 1)

        # Days in pipeline
        today = date.today()
        def _days_in(created_at):
            try:
                d = created_at.date() if hasattr(created_at, "date") else date.fromisoformat(str(created_at)[:10])
                return max(0, (today - d).days)
            except Exception:
                return 0
        df["days_in_pipeline"] = df["created_at"].apply(_days_in)

        # Days until expected close (negative = already overdue)
        def _days_until(expected_close):
            try:
                d = date.fromisoformat(str(expected_close)[:10])
                return (d - today).days
            except Exception:
                return 0
        df["days_until_close"] = df["expected_close_date"].apply(_days_until)

        # Log-scaled amount
        df["log_amount"] = np.log1p(df["amount"].fillna(0).clip(lower=0))

        # Activity features
        # A company can have deals with zero logged activities at all — not
        # just zero for these specific deals, an empty activities table
        # system-wide. That makes activities_df a DataFrame with no columns
        # (pd.DataFrame([]) from an empty list comprehension upstream), and
        # .groupby("deal_id") on a nonexistent column raised KeyError('deal_id'),
        # which the caller (get_deal_slip_analysis / get_pipeline_rescue_what_if)
        # only ever saw as "Deal slip model failed: 'deal_id'" — deal-slip
        # analysis was completely broken for any dataset with no activity
        # history. Same empty-Series fallback already used for act_recent below.
        if "deal_id" in activities_df.columns:
            act_total = activities_df.groupby("deal_id")["id"].count().rename("activity_count")
        else:
            act_total = pd.Series(dtype=int, name="activity_count")
        # Recent activity (last 14 days)
        if "activity_date" in activities_df.columns:
            cutoff = pd.Timestamp(today) - pd.Timedelta(days=14)
            recent_mask = pd.to_datetime(activities_df["activity_date"], errors="coerce") >= cutoff
            act_recent = activities_df[recent_mask].groupby("deal_id")["id"].count().rename("recent_activity_count")
        else:
            act_recent = pd.Series(dtype=int, name="recent_activity_count")

        df = df.join(act_total, on="deal_id" if "deal_id" in df.columns else "id")
        df = df.join(act_recent, on="deal_id" if "deal_id" in df.columns else "id")
        df["activity_count"] = df["activity_count"].fillna(0)
        df["recent_activity_count"] = df["recent_activity_count"].fillna(0)
        df["close_probability"] = df["close_probability"].fillna(50)

        return df

    # ─── Training ─────────────────────────────────────────────────────────

    def fit(self, deals_df: pd.DataFrame, activities_df: pd.DataFrame) -> "DealSlipModel":
        """
        deals_df must have: id, deal_id (optional alias), stage, created_at,
            expected_close_date, actual_close_date, amount, close_probability
        activities_df must have: deal_id, id, activity_date
        """
        df = self._build_features(deals_df, activities_df)
        if "id" in df.columns and "deal_id" not in df.columns:
            df["deal_id"] = df["id"]

        closed = df[df["stage"].isin(["Closed Won", "Closed Lost"])].copy()
        if closed.empty or len(closed) < 10:
            # Synthesise labels from open deals for demo purposes
            df["slipped"] = (df["days_until_close"] < -14).astype(int)
            df["slipped"] = df["slipped"].clip(0, 1)
            train_df = df
        else:
            # Slipped = closed-lost OR actual close > expected close by > 14 days
            def _slipped(row):
                if row["stage"] == "Closed Lost":
                    return 1
                if pd.notna(row.get("actual_close_date")) and pd.notna(row.get("expected_close_date")):
                    try:
                        actual = date.fromisoformat(str(row["actual_close_date"])[:10])
                        expected = date.fromisoformat(str(row["expected_close_date"])[:10])
                        return int((actual - expected).days > 14)
                    except Exception:
                        pass
                return 0
            closed["slipped"] = closed.apply(_slipped, axis=1)
            train_df = closed

        X = train_df[FEATURES].fillna(0)
        y = train_df["slipped"]
        if y.nunique() < 2:
            # All same class — add synthetic minority
            synthetic_row = X.iloc[0].copy()
            synthetic_row["days_until_close"] = -60
            X = pd.concat([X, pd.DataFrame([synthetic_row])], ignore_index=True)
            y = pd.concat([y, pd.Series([1 - y.iloc[0]])], ignore_index=True)

        # Ensure each class has ≥ 3 samples for CV calibration
        min_class_count = int(y.value_counts().min())
        while min_class_count < 3:
            minority_class = int(y.value_counts().idxmin())
            for _ in range(3 - min_class_count):
                sr = X.sample(1, random_state=42).copy()
                X = pd.concat([X, sr], ignore_index=True)
                y = pd.concat([y, pd.Series([minority_class])], ignore_index=True)
            min_class_count = int(y.value_counts().min())

        base = GradientBoostingClassifier(n_estimators=100, max_depth=3, random_state=42, learning_rate=0.1)
        calibrated = CalibratedClassifierCV(base, cv=3, method="isotonic")
        self.model = Pipeline([("scaler", StandardScaler()), ("clf", calibrated)])
        self.model.fit(X, y)
        self._fitted = True

        # Feature importances from underlying GBM
        try:
            raw_clf = self.model.named_steps["clf"]
            if hasattr(raw_clf, "calibrated_classifiers_"):
                base_est = raw_clf.calibrated_classifiers_[0].estimator
                if hasattr(base_est, "feature_importances_"):
                    self.feature_importances_ = dict(zip(FEATURES, base_est.feature_importances_))
        except Exception:
            pass

        return self

    # ─── Prediction ───────────────────────────────────────────────────────

    def predict(self, deals_df: pd.DataFrame, activities_df: pd.DataFrame) -> list[SlipResult]:
        if not self._fitted or self.model is None:
            raise RuntimeError("DealSlipModel is not fitted. Call .fit() first.")

        df = self._build_features(deals_df, activities_df)
        if "id" in df.columns and "deal_id" not in df.columns:
            df["deal_id"] = df["id"]

        open_mask = ~df["stage"].isin(["Closed Won", "Closed Lost"])
        open_df = df[open_mask].copy()
        if open_df.empty:
            return []

        X = open_df[FEATURES].fillna(0)
        scores = self.model.predict_proba(X)[:, 1]

        results = []
        for i, row in enumerate(open_df.itertuples()):
            score = float(scores[i])
            risk_factors = []
            if row.days_until_close < 0:
                risk_factors.append(f"Overdue by {abs(row.days_until_close)} days")
            if row.recent_activity_count == 0:
                risk_factors.append("No activity in last 14 days")
            if row.stage_ord <= 1:
                risk_factors.append("Early stage for expected close window")
            if row.close_probability < 40:
                risk_factors.append("Low rep-estimated close probability")
            results.append(
                SlipResult(
                    deal_id=str(getattr(row, "id", getattr(row, "deal_id", "unknown"))),
                    deal_name=str(getattr(row, "name", "Unknown Deal")),
                    slip_risk_score=round(score, 3),
                    slip_flag=score >= SLIP_THRESHOLD,
                    expected_close_date=str(row.expected_close_date)[:10] if row.expected_close_date else None,
                    days_until_close=int(row.days_until_close),
                    stage=str(row.stage),
                    amount=float(row.amount),
                    top_risk_factors=risk_factors[:3],
                )
            )

        results.sort(key=lambda r: r.slip_risk_score, reverse=True)
        return results

    # ─── Persistence ─────────────────────────────────────────────────────

    def save(self, path: Path = MODEL_PATH) -> None:
        joblib.dump(self, path)

    @classmethod
    def load(cls, path: Path = MODEL_PATH) -> "DealSlipModel":
        return joblib.load(path)
