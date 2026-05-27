"""
backend/statistics/funnel_analysis.py
====================================
Funnel analysis functions for stage-based metrics
"""
import pandas as pd
from typing import Optional


def stage_conversion_rates(
    df: pd.DataFrame,
    stage_col: str,
    id_col: str,
    stage_order: Optional[list[str]] = None,
) -> pd.DataFrame:
    """
    Calculate conversion rates between sequential stages.
    
    Args:
        df: DataFrame with stage and ID columns
        stage_col: Column containing stage names
        id_col: Column with unique IDs (deals, opportunities, etc.)
        stage_order: Ordered list of stages (optional)
    
    Returns:
        DataFrame with conversion rates between stages
    """
    # Get unique IDs per stage
    stage_counts = df.groupby(stage_col)[id_col].nunique().reset_index()
    stage_counts.columns = [stage_col, 'count']
    
    # Order stages if provided
    if stage_order:
        stage_counts[stage_col] = pd.Categorical(
            stage_counts[stage_col],
            categories=stage_order,
            ordered=True,
        )
        stage_counts = stage_counts.sort_values(stage_col)
    
    # Calculate conversion rates
    stage_counts['cumulative_count'] = stage_counts['count'].cumsum()
    stage_counts['conversion_from_first'] = (
        stage_counts['count'] / stage_counts.iloc[0]['count'] * 100
    ).round(2)
    
    # Conversion between consecutive stages
    stage_counts['prev_count'] = stage_counts['count'].shift(1)
    stage_counts['stage_conversion'] = (
        (stage_counts['count'] / stage_counts['prev_count'] * 100)
        .round(2)
    ).fillna(100)
    
    return stage_counts


def stage_dropoff(
    df: pd.DataFrame,
    stage_col: str,
    id_col: str,
    stage_order: Optional[list[str]] = None,
) -> pd.DataFrame:
    """
    Calculate dropoff (loss) at each stage.
    
    Args:
        df: DataFrame with stage and ID columns
        stage_col: Column containing stage names
        id_col: Column with unique IDs
        stage_order: Ordered list of stages (optional)
    
    Returns:
        DataFrame with dropoff analysis
    """
    # Get conversion rates first
    conversions = stage_conversion_rates(df, stage_col, id_col, stage_order)
    
    # Calculate dropoff
    conversions['dropoff_count'] = conversions['prev_count'] - conversions['count']
    conversions['dropoff_pct'] = (
        conversions['dropoff_count'] / conversions['prev_count'] * 100
    ).round(2).fillna(0)
    
    return conversions[[
        stage_col, 'count', 'dropoff_count', 'dropoff_pct', 'conversion_from_first'
    ]]
