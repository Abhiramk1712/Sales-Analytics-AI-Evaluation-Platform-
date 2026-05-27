"""
backend/ingestion/ingestion_run.py
==================================
Ingestion run tracking and metadata
"""
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Optional
import hashlib


@dataclass
class IngestionRun:
    """
    Represents a single data ingestion run.
    Tracks source file, timing, row count, columns, and data quality indicators.
    """
    
    source_file: str
    loaded_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    row_count: int = 0
    column_count: int = 0
    columns: list[str] = field(default_factory=list)
    schema_hash: Optional[str] = None
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    
    def compute_schema_hash(self) -> str:
        """
        Compute a hash of the column schema.
        Used to detect schema changes between runs.
        """
        schema_str = ",".join(sorted(self.columns))
        hash_obj = hashlib.md5(schema_str.encode())
        self.schema_hash = hash_obj.hexdigest()
        return self.schema_hash
    
    def add_error(self, message: str) -> None:
        """Add an error message."""
        self.errors.append(message)
    
    def add_warning(self, message: str) -> None:
        """Add a warning message."""
        self.warnings.append(message)
    
    def has_errors(self) -> bool:
        """Check if this run has errors."""
        return len(self.errors) > 0
    
    def has_warnings(self) -> bool:
        """Check if this run has warnings."""
        return len(self.warnings) > 0
    
    def summary(self) -> dict:
        """Return a summary dict of the ingestion run."""
        return {
            "source_file": self.source_file,
            "loaded_at": self.loaded_at.isoformat(),
            "row_count": self.row_count,
            "column_count": self.column_count,
            "columns": self.columns,
            "schema_hash": self.schema_hash,
            "error_count": len(self.errors),
            "warning_count": len(self.warnings),
            "errors": self.errors,
            "warnings": self.warnings,
        }
