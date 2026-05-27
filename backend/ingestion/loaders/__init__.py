"""
backend/ingestion/loaders/__init__.py
=====================================
Data loader implementations
"""
from backend.ingestion.loaders.csv_loader import CSVLoader
from backend.ingestion.loaders.pdf_loader import PDFLoader

__all__ = ["CSVLoader", "PDFLoader"]
