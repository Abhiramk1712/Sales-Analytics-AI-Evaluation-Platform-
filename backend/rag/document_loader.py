"""
backend/rag/document_loader.py
==============================
Load knowledge base documents
"""
import os
from pathlib import Path
from typing import List, Dict, Any


class DocumentLoader:
    """Loads markdown documents from knowledge base."""
    
    def __init__(self, knowledge_base_dir: str = "docs/knowledge_base"):
        self.kb_dir = Path(knowledge_base_dir)
    
    def load_all(self) -> List[Dict[str, Any]]:
        """Load all markdown files from knowledge base."""
        documents = []
        
        if not self.kb_dir.exists():
            return documents
        
        for md_file in self.kb_dir.glob("*.md"):
            try:
                with open(md_file, "r", encoding="utf-8") as f:
                    content = f.read()
                documents.append({
                    "source": md_file.name,
                    "path": str(md_file),
                    "content": content,
                })
            except Exception as e:
                print(f"Error loading {md_file}: {e}")
        
        return documents
    
    def load_file(self, filename: str) -> Dict[str, Any]:
        """Load a specific file."""
        file_path = self.kb_dir / filename
        if not file_path.exists():
            raise FileNotFoundError(f"{filename} not found in knowledge base")
        
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
        
        return {
            "source": filename,
            "path": str(file_path),
            "content": content,
        }
