"""
backend/validation/validators.py
================================
Data validation functions and quality checks
"""
import pandas as pd
import numpy as np
from typing import Optional, Set
from backend.validation.quality_report import DataQualityReport, CheckResult


class DataQualityValidator:
    """
    Validates data quality against common issues.
    """
    
    def __init__(
        self,
        required_columns: Optional[list[str]] = None,
        id_columns: Optional[list[str]] = None,
    ):
        """
        Initialize validator.
        
        Args:
            required_columns: List of columns that must exist
            id_columns: List of ID columns that should be unique
        """
        self.required_columns = required_columns or []
        self.id_columns = id_columns or []
    
    def validate(
        self,
        df: pd.DataFrame,
        row_count: int = 0,
        column_count: int = 0,
    ) -> DataQualityReport:
        """
        Run all quality checks on a dataframe.
        
        Args:
            df: DataFrame to validate
            row_count: Optional expected row count (for metadata)
            column_count: Optional expected column count (for metadata)
        
        Returns:
            DataQualityReport object
        """
        report = DataQualityReport(
            status="PASS",
            row_count=row_count or len(df),
            column_count=column_count or len(df.columns),
        )
        
        # Check 1: Required columns
        if self.required_columns:
            missing = self._check_required_columns(df)
            if missing:
                result = CheckResult(
                    check_name="required_columns",
                    status="FAIL",
                    message=f"Missing required columns: {missing}",
                    affected_rows=0,
                )
                report.add_check(result)
        
        # Check 2: Duplicate IDs
        if self.id_columns and len(df) > 0:
            duplicates = self._check_duplicate_ids(df)
            if duplicates:
                result = CheckResult(
                    check_name="duplicate_ids",
                    status="WARN",
                    message=f"Found {len(duplicates)} duplicate ID values",
                    affected_rows=len(duplicates),
                    details={"duplicate_ids": duplicates[:10]},  # Sample
                )
                report.add_check(result)
        
        # Check 3: Null foreign keys
        nullable_columns = df.select_dtypes(include=['object', 'float']).columns
        null_counts = df[nullable_columns].isnull().sum()
        if (null_counts > 0).any():
            null_col = null_counts[null_counts > 0]
            result = CheckResult(
                check_name="null_values",
                status="WARN",
                message=f"Found null values in {len(null_col)} columns",
                affected_rows=int(null_col.sum()),
                details={"null_columns": null_col.to_dict()},
            )
            report.add_check(result)
        
        # Check 4: Negative numeric values (where not expected)
        numeric_cols = df.select_dtypes(include=[np.number]).columns
        for col in numeric_cols:
            if "amount" in col.lower() or "revenue" in col.lower() or "deal" in col.lower():
                negative_count = (df[col] < 0).sum()
                if negative_count > 0:
                    result = CheckResult(
                        check_name="negative_values",
                        status="WARN",
                        message=f"Column '{col}' has {negative_count} negative values",
                        affected_rows=int(negative_count),
                    )
                    report.add_check(result)
        
        # Check 5: Empty dataframe
        if len(df) == 0:
            result = CheckResult(
                check_name="empty_dataframe",
                status="FAIL",
                message="Dataframe is empty",
                affected_rows=0,
            )
            report.add_check(result)
        
        # Update overall status
        report.update_status()
        
        return report
    
    def _check_required_columns(self, df: pd.DataFrame) -> list[str]:
        """Check for missing required columns."""
        missing = [col for col in self.required_columns if col not in df.columns]
        return missing
    
    def _check_duplicate_ids(self, df: pd.DataFrame) -> list[str]:
        """Check for duplicate ID values across ID columns."""
        duplicates = []
        for col in self.id_columns:
            if col in df.columns:
                dup_mask = df[col].duplicated(keep=False)
                dup_values = df[dup_mask][col].unique().tolist()
                duplicates.extend(dup_values)
        return list(set(duplicates))
