"""Unified ETL pipeline: bronze -> silver -> gold -> feature store."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from backend.etl.contracts import DATA_CONTRACTS
from backend.etl.marts import build_gold_marts
from backend.etl.quality import evaluate_table_quality


@dataclass
class ETLResult:
    bronze: dict[str, dict[str, Any]]
    silver: dict[str, pd.DataFrame]
    gold: dict[str, dict[str, Any]]
    feature_store: dict[str, pd.DataFrame]
    quality: dict[str, dict[str, Any]]
    generated_at: str


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_csv_table(csv_path: Path) -> pd.DataFrame:
    if not csv_path.exists() or csv_path.stat().st_size == 0:
        return pd.DataFrame()
    return pd.read_csv(csv_path)


def _clean_table(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    out = df.copy()
    out.columns = [str(c).strip() for c in out.columns]
    for col in out.columns:
        if out[col].dtype == object:
            out[col] = out[col].astype(str).str.strip()
    out = out.drop_duplicates()
    return out


def run_etl_pipeline(source_dir: str | Path) -> ETLResult:
    source = Path(source_dir)
    if not source.exists():
        raise FileNotFoundError(f"ETL source directory does not exist: {source}")

    bronze: dict[str, dict[str, Any]] = {}
    silver: dict[str, pd.DataFrame] = {}
    quality: dict[str, dict[str, Any]] = {}

    for entity in DATA_CONTRACTS.keys():
        csv_path = source / f"{entity}.csv"
        raw_df = _read_csv_table(csv_path)
        bronze[entity] = {
            "path": str(csv_path),
            "row_count": int(len(raw_df)),
            "columns": list(raw_df.columns),
            "loaded_at": _now_iso(),
        }

        clean_df = _clean_table(raw_df)
        silver[entity] = clean_df

        issues, score = evaluate_table_quality(entity, clean_df)
        quality[entity] = {
            "issues": [issue.__dict__ for issue in issues],
            "data_quality_score": score,
            "row_count": int(len(clean_df)),
        }

    gold = build_gold_marts(silver)

    feature_store: dict[str, pd.DataFrame] = {}
    if "mart_forecast_features" in gold:
        feature_store["forecast_revenue_monthly"] = gold["mart_forecast_features"]["data"]
    if "mart_rep_month_performance" in gold:
        feature_store["rep_performance_monthly"] = gold["mart_rep_month_performance"]["data"]

    return ETLResult(
        bronze=bronze,
        silver=silver,
        gold=gold,
        feature_store=feature_store,
        quality=quality,
        generated_at=_now_iso(),
    )
