"""
ml/rep_clustering.py
====================
Sales Rep Performance Clustering
----------------------------------
Groups reps into behavioural personas using K-Means clustering.
Applies PCA for 2-D visualisation and silhouette scoring to
select the optimal number of clusters.

Cluster personas produced
-------------------------
- "Top Performers"  — high attainment, high win rate, short cycle
- "High Volume"     — many deals, mid attainment, high activity
- "Rising Stars"    — improving trend, above-median engagement
- "At-Risk Reps"    — low attainment, low pipeline, high churn rate

Academic note: Demonstrates unsupervised learning, dimensionality
reduction (PCA), and cluster evaluation (silhouette, elbow method).
"""
import numpy as np
import pandas as pd
import joblib
from pathlib import Path
from dataclasses import dataclass, field
from sklearn.cluster import KMeans
from sklearn.preprocessing import StandardScaler
from sklearn.decomposition import PCA
from sklearn.metrics import silhouette_score
from scipy.spatial.distance import cdist

MODEL_PATH = Path(__file__).parent / "saved" / "rep_clusters.pkl"
MODEL_PATH.parent.mkdir(exist_ok=True)

FEATURES = [
    "attainment_pct", "win_rate", "avg_deal_size",
    "pipeline_coverage", "avg_sales_cycle", "activity_rate",
]

PERSONA_LABELS = {
    0: "Top Performer",
    1: "High Volume",
    2: "Rising Star",
    3: "Needs Coaching",
    4: "Quota At Risk",
}

# Thresholds for Quota At Risk: low attainment, low pipeline, low activity
QUOTA_AT_RISK_ATTAINMENT_MAX = 60.0   # < 60% attainment
QUOTA_AT_RISK_PIPELINE_MAX   = 1.5    # pipeline coverage < 1.5×
QUOTA_AT_RISK_ACTIVITY_MAX   = 2.0    # activity rate < 2 per deal


@dataclass
class ClusterResult:
    rep_id:         str
    rep_name:       str
    cluster_id:     int
    persona:        str
    pca_x:          float
    pca_y:          float
    features:       dict  = field(default_factory=dict)


class RepClusteringModel:
    """K-Means clustering with automatic k selection via silhouette score."""

    def __init__(self, k_range: tuple = (3, 6)):
        self.k_range  = k_range
        self.scaler   = StandardScaler()
        self.pca      = PCA(n_components=2, random_state=42)
        self._fitted  = False

    def fit(self, reps_df: pd.DataFrame) -> "RepClusteringModel":
        """
        reps_df columns: rep_id, name, attainment_pct, win_rate,
                         avg_deal_size, pipeline_coverage,
                         avg_sales_cycle, activity_rate
        """
        df = reps_df.copy()
        # Invert sales cycle so higher = better for all features
        df["avg_sales_cycle"] = 1 / (df["avg_sales_cycle"].clip(lower=1))

        self.df_ = df
        X = df[FEATURES].fillna(df[FEATURES].median())
        X_scaled = self.scaler.fit_transform(X)
        n_samples = len(X_scaled)

        # ── Optimal k via silhouette ───────────────────────────────────
        max_valid_k = min(self.k_range[1], n_samples - 1)
        min_valid_k = max(2, min(self.k_range[0], max_valid_k))
        best_k, best_score = min_valid_k, -1
        self.silhouette_scores_ = {}
        for k in range(min_valid_k, max_valid_k + 1):
            km = KMeans(n_clusters=k, random_state=42, n_init=10)
            labels = km.fit_predict(X_scaled)
            if len(set(labels)) > 1:
                try:
                    score = silhouette_score(X_scaled, labels)
                except ValueError:
                    continue
                self.silhouette_scores_[k] = round(float(score), 4)
                if score > best_score:
                    best_k, best_score = k, score

        self.best_k_ = best_k
        self.kmeans_ = KMeans(n_clusters=best_k, random_state=42, n_init=10)
        self.labels_  = self.kmeans_.fit_predict(X_scaled)
        self.X_scaled_ = X_scaled

        # ── PCA for 2-D scatter ───────────────────────────────────────
        self.pca_coords_ = self.pca.fit_transform(X_scaled)
        self.pca_variance_ = list(np.round(self.pca.explained_variance_ratio_ * 100, 2))

        # ── Auto-assign persona labels ────────────────────────────────
        centers = self.kmeans_.cluster_centers_
        center_df = pd.DataFrame(self.scaler.inverse_transform(centers), columns=FEATURES)
        self.persona_map_ = self._assign_personas(center_df)

        self._fitted = True
        return self

    def _assign_personas(self, center_df: pd.DataFrame) -> dict[int, str]:
        """Heuristically assign persona labels based on centroid values."""
        labels = {}
        attain_rank   = center_df["attainment_pct"].rank(ascending=False)
        volume_rank   = center_df["win_rate"].rank(ascending=True)   # low win-rate → high volume proxy
        pipeline_rank = center_df["pipeline_coverage"].rank(ascending=False)

        for i in range(len(center_df)):
            attainment    = center_df.loc[i, "attainment_pct"]
            pipeline_cov  = center_df.loc[i, "pipeline_coverage"]
            activity      = center_df.loc[i, "activity_rate"]
            # Quota At Risk: declining pipeline + low activity + <60% attainment at midpoint
            if (
                attainment < QUOTA_AT_RISK_ATTAINMENT_MAX
                and pipeline_cov < QUOTA_AT_RISK_PIPELINE_MAX
                and activity < QUOTA_AT_RISK_ACTIVITY_MAX
            ):
                labels[i] = "Quota At Risk"
            elif attain_rank[i] == 1:
                labels[i] = "Top Performer"
            elif pipeline_rank[i] == 1 and attain_rank[i] <= 2:
                labels[i] = "High Volume"
            elif attainment < 70:
                labels[i] = "Needs Coaching"
            else:
                labels[i] = "Rising Star"
        return labels

    def get_results(self) -> list[ClusterResult]:
        assert self._fitted
        results = []
        for i, (_, row) in enumerate(self.df_.iterrows()):
            cluster_id = int(self.labels_[i])
            results.append(ClusterResult(
                rep_id     = str(row.get("rep_id", i)),
                rep_name   = str(row.get("name", f"Rep {i}")),
                cluster_id = cluster_id,
                persona    = self.persona_map_.get(cluster_id, f"Group {cluster_id}"),
                pca_x      = round(float(self.pca_coords_[i, 0]), 4),
                pca_y      = round(float(self.pca_coords_[i, 1]), 4),
                features   = {f: round(float(row[f]), 2) for f in FEATURES if f in row},
            ))
        return results

    def get_diagnostics(self) -> dict:
        return {
            "optimal_k":         self.best_k_,
            "silhouette_scores": self.silhouette_scores_,
            "pca_variance_pct":  self.pca_variance_,
            "persona_map":       self.persona_map_,
        }

    def save(self):
        joblib.dump(self, MODEL_PATH)

    @classmethod
    def load(cls) -> "RepClusteringModel":
        return joblib.load(MODEL_PATH)


# ── Convenience function used by API ──────────────────────────────────────
def run_rep_clustering(reps: list[dict]) -> dict:
    df     = pd.DataFrame(reps)
    model  = RepClusteringModel()
    model.fit(df)
    model.save()
    results = model.get_results()
    diag    = model.get_diagnostics()
    return {
        "model_info":   "K-Means with silhouette-optimised k + PCA 2D projection",
        "diagnostics":  diag,
        "clusters": [
            {"rep_id": r.rep_id, "rep_name": r.rep_name, "cluster_id": r.cluster_id,
             "persona": r.persona, "pca_x": r.pca_x, "pca_y": r.pca_y,
             "features": r.features}
            for r in results
        ],
    }
