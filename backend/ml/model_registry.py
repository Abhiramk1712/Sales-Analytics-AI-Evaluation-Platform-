"""
backend/ml/model_registry.py
=============================
ML Model registry for tracking trained models and their metadata
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, Dict, Any


def _utc_now_naive() -> datetime:
    """Return UTC timestamp as naive datetime for DB columns without timezone."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ── Canonical model name constants ────────────────────────────────────────
# Use these everywhere: MLPrediction.model_name, ModelRunRecord.model_name,
# model_registry.ModelRun.model_name — ensures consistent filtering.
MODEL_REVENUE_FORECAST = "revenue_forecast"
MODEL_DEAL_SCORING     = "deal_scoring"
MODEL_REP_CLUSTERING   = "rep_clustering"


@dataclass
class ModelRun:
    """Metadata for a trained model."""
    
    model_name: str  # 'deal_scorer', 'forecaster', 'rep_clusterer'
    model_version: str
    trained_at: datetime = field(default_factory=_utc_now_naive)
    training_rows: int = 0
    feature_names: list[str] = field(default_factory=list)
    target_name: Optional[str] = None
    metrics: Dict[str, Any] = field(default_factory=dict)  # accuracy, auc, rmse, etc.
    limitations: Optional[list[str]] = None
    artifact_path: Optional[str] = None
    data_hash: Optional[str] = None
    notes: Optional[str] = None
    
    def summary(self) -> Dict[str, Any]:
        """Return a summary dict."""
        return {
            "model_name": self.model_name,
            "model_version": self.model_version,
            "trained_at": self.trained_at.isoformat(),
            "training_rows": self.training_rows,
            "feature_count": len(self.feature_names),
            "feature_names": self.feature_names,
            "target": self.target_name,
            "metrics": self.metrics,
            "limitations": self.limitations or [],
            "artifact_path": self.artifact_path,
            "data_hash": self.data_hash,
            "notes": self.notes,
        }
