"""
backend/ingestion/loaders/pdf_loader.py
======================================
PDF table loader for ingestion. Extracts structured rows from table-like pages.
"""
from __future__ import annotations

from typing import Any


class PDFLoader:
    """Extract structured rows from PDF tables for ingestion."""

    def __init__(self) -> None:
        try:
            import pdfplumber  # type: ignore
        except Exception as exc:  # pragma: no cover - depends on optional package
            raise RuntimeError("pdfplumber is required for PDF ingestion") from exc
        self._pdfplumber = pdfplumber

    @staticmethod
    def _normalize_headers(raw_headers: list[Any]) -> list[str]:
        headers: list[str] = []
        for idx, raw in enumerate(raw_headers):
            text = str(raw or "").strip()
            headers.append(text if text else f"column_{idx + 1}")
        return headers

    @staticmethod
    def _row_to_dict(headers: list[str], row: list[Any]) -> dict[str, str]:
        values = list(row) + [""] * max(0, len(headers) - len(row))
        return {
            header: str(values[idx] or "").strip()
            for idx, header in enumerate(headers)
        }

    def extract_rows(self, file_path: str, max_rows: int = 5000) -> list[dict[str, str]]:
        rows: list[dict[str, str]] = []

        with self._pdfplumber.open(file_path) as pdf:
            for page in pdf.pages:
                tables = page.extract_tables() or []
                for table in tables:
                    if not table or len(table) < 2:
                        continue
                    headers = self._normalize_headers(table[0])
                    for raw_row in table[1:]:
                        row_dict = self._row_to_dict(headers, raw_row or [])
                        if any(v for v in row_dict.values()):
                            rows.append(row_dict)
                        if len(rows) >= max_rows:
                            return rows

        return rows

    def preview_rows(self, file_path: str, limit: int = 200) -> list[dict[str, str]]:
        return self.extract_rows(file_path=file_path, max_rows=limit)
