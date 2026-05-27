"""
backend/statistics/driver_analysis.py
=====================================
Driver analysis functions for understanding what contributes to metrics
"""
import pandas as pd
from typing import Optional


def contribution_analysis(
    df: pd.DataFrame,
    group_col: str,
    metric_col: str,
) -> pd.DataFrame:
    """
    Analyze contribution of each group to a metric.
    
    Args:
        df: DataFrame with grouping and metric columns
        group_col: Column to group by
        metric_col: Metric column to sum
    
    Returns:
        DataFrame with contribution analysis
    """
    grouped = df.groupby(group_col)[metric_col].sum().reset_index()
    total = grouped[metric_col].sum()
    grouped['contribution_pct'] = (grouped[metric_col] / total * 100).round(2)
    grouped = grouped.sort_values(metric_col, ascending=False)
    return grouped


def compare_periods(
    df: pd.DataFrame,
    period_col: str,
    metric_col: str,
    current_period: str,
    previous_period: str,
) -> dict:
    """
    Compare a metric between two periods.
    
    Args:
        df: DataFrame with period and metric columns
        period_col: Column containing period identifiers
        metric_col: Metric column to compare
        current_period: Current period identifier
        previous_period: Previous period identifier
    
    Returns:
        Dictionary with comparison results
    """
    current_val = df[df[period_col] == current_period][metric_col].sum()
    previous_val = df[df[period_col] == previous_period][metric_col].sum()
    
    if previous_val == 0:
        pct_change = None
    else:
        pct_change = ((current_val - previous_val) / previous_val) * 100
    
    return {
        "current_period": current_period,
        "current_value": float(current_val),
        "previous_period": previous_period,
        "previous_value": float(previous_val),
        "absolute_change": float(current_val - previous_val),
        "percent_change": pct_change,
    }
