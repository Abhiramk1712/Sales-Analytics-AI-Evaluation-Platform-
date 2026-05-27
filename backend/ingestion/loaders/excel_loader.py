"""
backend/ingestion/loaders/excel_loader.py
==========================================
Excel (.xlsx / .xls) file data loader.
Each sheet is treated as a separate table candidate.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import pandas as pd

from backend.ingestion.ingestion_run import IngestionRun


class ExcelLoader:
    """Load one or more sheets from an Excel file."""

    def load(
        self,
        file_path: str,
        sheet_name: str | int | None = None,
        nrows: Optional[int] = None,
    ) -> tuple[pd.DataFrame, IngestionRun]:
        """Load a single sheet (default: first sheet).

        Args:
            file_path: Path to .xlsx / .xls file
            sheet_name: Sheet name or 0-based index. None → first sheet.
            nrows: Optional row limit.

        Returns:
            Tuple of (DataFrame, IngestionRun)
        """
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")

        df = pd.read_excel(
            path,
            sheet_name=0 if sheet_name is None else sheet_name,
            nrows=nrows,
            engine="openpyxl" if path.suffix.lower() == ".xlsx" else None,
        )

        # Normalise column names: strip whitespace
        df.columns = [str(c).strip() for c in df.columns]

        run = IngestionRun(
            source_file=str(path.absolute()),
            row_count=len(df),
            column_count=len(df.columns),
            columns=df.columns.tolist(),
        )
        run.compute_schema_hash()
        return df, run

    def load_all_sheets(
        self,
        file_path: str,
        nrows: Optional[int] = None,
    ) -> dict[str, tuple[pd.DataFrame, IngestionRun]]:
        """Load every sheet, returning {sheet_name: (df, run)}."""
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")

        all_sheets: dict[str, pd.DataFrame] = pd.read_excel(
            path,
            sheet_name=None,
            nrows=nrows,
            engine="openpyxl" if path.suffix.lower() == ".xlsx" else None,
        )  # type: ignore[assignment]

        result: dict[str, tuple[pd.DataFrame, IngestionRun]] = {}
        for name, df in all_sheets.items():
            df.columns = [str(c).strip() for c in df.columns]
            run = IngestionRun(
                source_file=f"{path.absolute()}#{name}",
                row_count=len(df),
                column_count=len(df.columns),
                columns=df.columns.tolist(),
            )
            run.compute_schema_hash()
            result[str(name)] = (df, run)
        return result
