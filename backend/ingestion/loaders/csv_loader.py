"""
backend/ingestion/loaders/csv_loader.py
========================================
CSV file data loader
"""
from pathlib import Path
from typing import Optional
import pandas as pd
from backend.ingestion.ingestion_run import IngestionRun


class CSVLoader:
    """
    Loads data from CSV files.
    
    Returns a dataframe plus metadata about the ingestion run.
    """
    
    def __init__(self, delimiter: str = ",", encoding: str = "utf-8"):
        """
        Initialize CSV loader.
        
        Args:
            delimiter: CSV delimiter character
            encoding: File encoding
        """
        self.delimiter = delimiter
        self.encoding = encoding
    
    def load(
        self,
        file_path: str,
        nrows: Optional[int] = None,
    ) -> tuple[pd.DataFrame, IngestionRun]:
        """
        Load a CSV file.
        
        Args:
            file_path: Path to CSV file
            nrows: Optional limit on rows to read
        
        Returns:
            Tuple of (dataframe, IngestionRun)
        
        Raises:
            FileNotFoundError: If file does not exist
            pd.errors.ParserError: If CSV is malformed
        """
        path = Path(file_path)
        
        if not path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")
        
        # Read CSV
        df = pd.read_csv(
            path,
            delimiter=self.delimiter,
            encoding=self.encoding,
            nrows=nrows,
        )
        
        # Create ingestion run metadata
        run = IngestionRun(
            source_file=str(path.absolute()),
            row_count=len(df),
            column_count=len(df.columns),
            columns=df.columns.tolist(),
        )
        
        # Compute schema hash
        run.compute_schema_hash()
        
        return df, run
