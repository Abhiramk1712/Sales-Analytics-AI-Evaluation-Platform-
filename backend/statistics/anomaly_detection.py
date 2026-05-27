"""
backend/statistics/anomaly_detection.py
=======================================
Anomaly detection functions
"""
from typing import List
import numpy as np
from scipy import stats


def zscore_outliers(values: List[float], threshold: float = 3.0) -> List[int]:
    """
    Detect outliers using Z-score method.
    
    Args:
        values: List of numeric values
        threshold: Z-score threshold (default 3.0 = ~99.7% confidence)
    
    Returns:
        List of indices of detected outliers
    """
    values = np.array(values)
    values_clean = values[~np.isnan(values)]
    
    if len(values_clean) < 2:
        return []
    
    z_scores = np.abs(stats.zscore(values_clean))
    outliers = np.where(z_scores > threshold)[0].tolist()
    
    return outliers


def iqr_outliers(values: List[float], multiplier: float = 1.5) -> List[int]:
    """
    Detect outliers using Interquartile Range method.
    
    Args:
        values: List of numeric values
        multiplier: IQR multiplier (default 1.5)
    
    Returns:
        List of indices of detected outliers
    """
    values = np.array(values)
    values_clean = values[~np.isnan(values)]
    
    if len(values_clean) < 4:
        return []
    
    q1 = np.percentile(values_clean, 25)
    q3 = np.percentile(values_clean, 75)
    iqr = q3 - q1
    
    lower_bound = q1 - (multiplier * iqr)
    upper_bound = q3 + (multiplier * iqr)
    
    outliers = np.where((values_clean < lower_bound) | (values_clean > upper_bound))[0].tolist()
    
    return outliers


def detect_metric_spikes(series: List[float], threshold_pct: float = 30) -> List[int]:
    """
    Detect abrupt percentage spikes between consecutive points.

    Returns indices where pct change exceeds threshold_pct in absolute terms.
    """
    if not series or len(series) < 2:
        return []

    spikes: List[int] = []
    for idx in range(1, len(series)):
        prev = series[idx - 1]
        curr = series[idx]
        if prev == 0:
            if curr != 0:
                spikes.append(idx)
            continue
        pct = abs(((curr - prev) / prev) * 100)
        if pct >= threshold_pct:
            spikes.append(idx)
    return spikes
