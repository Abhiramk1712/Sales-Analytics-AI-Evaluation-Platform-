"""
backend/statistics/descriptive.py
==================================
Descriptive statistics functions
"""
from typing import Optional, List
import numpy as np
from scipy import stats


def summarize_distribution(values: List[float]) -> dict:
    """
    Summarize the distribution of numeric values.
    
    Args:
        values: List of numeric values
    
    Returns:
        Dictionary with summary statistics
    """
    values = np.array(values)
    values = values[~np.isnan(values)]  # Remove NaNs
    
    if len(values) == 0:
        return {
            "count": 0,
            "mean": None,
            "median": None,
            "std": None,
            "min": None,
            "max": None,
            "p25": None,
            "p75": None,
        }
    
    return {
        "count": len(values),
        "mean": float(np.mean(values)),
        "median": float(np.median(values)),
        "std": float(np.std(values)),
        "min": float(np.min(values)),
        "max": float(np.max(values)),
        "p25": float(np.percentile(values, 25)),
        "p75": float(np.percentile(values, 75)),
    }


def percentile_rank(value: float, values: List[float]) -> float:
    """
    Calculate the percentile rank of a value within a distribution.
    
    Args:
        value: Value to rank
        values: Reference distribution
    
    Returns:
        Percentile rank (0-100)
    """
    values = np.array(values)
    values = values[~np.isnan(values)]
    
    if len(values) == 0:
        return 0.0
    
    rank = stats.percentileofscore(values, value)
    return float(rank)


def month_over_month_change(current: float, previous: float) -> dict:
    """
    Calculate month-over-month change metrics.
    
    Args:
        current: Current period value
        previous: Previous period value
    
    Returns:
        Dictionary with absolute and percentage change
    """
    if previous == 0:
        pct_change = None if current == 0 else float('inf')
    else:
        pct_change = ((current - previous) / previous) * 100
    
    return {
        "current": current,
        "previous": previous,
        "absolute_change": current - previous,
        "percent_change": pct_change,
        "is_increase": current > previous,
    }
