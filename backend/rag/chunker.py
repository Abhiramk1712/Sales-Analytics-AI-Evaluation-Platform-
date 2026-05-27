"""
backend/rag/chunker.py
======================
Split documents into chunks for retrieval
"""
import re
from typing import List, Dict, Any


class DocumentChunker:
    """Chunks documents by headings and character length."""
    
    def __init__(self, chunk_size: int = 500, overlap: int = 100):
        self.chunk_size = chunk_size
        self.overlap = overlap
    
    def chunk(self, documents: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Chunk documents into retrievable units.
        
        Args:
            documents: List of documents with 'content' field
        
        Returns:
            List of chunks with source, heading, and content
        """
        chunks = []
        
        for doc in documents:
            source = doc.get("source", "unknown")
            content = doc.get("content", "")
            
            # Split by headings (## or ###)
            sections = re.split(r'\n(##+ .+)\n', content)
            
            current_heading = "intro"
            current_text = ""
            
            for i, section in enumerate(sections):
                if section.startswith("##"):
                    # This is a heading
                    current_heading = section.strip()
                else:
                    # This is content
                    current_text += section
                    
                    # When we have enough text, create a chunk
                    if len(current_text) >= self.chunk_size:
                        # Split further if still too long
                        sub_chunks = self._split_text(current_text)
                        for sub_chunk in sub_chunks:
                            chunks.append({
                                "source": source,
                                "heading": current_heading,
                                "content": sub_chunk.strip(),
                            })
                        current_text = ""
            
            # Don't forget remaining text
            if current_text.strip():
                chunks.append({
                    "source": source,
                    "heading": current_heading,
                    "content": current_text.strip(),
                })
        
        return chunks
    
    def _split_text(self, text: str) -> List[str]:
        """Split text into chunks."""
        if len(text) <= self.chunk_size:
            return [text]
        
        chunks = []
        words = text.split()
        current_chunk = []
        current_length = 0
        
        for word in words:
            word_len = len(word) + 1
            if current_length + word_len > self.chunk_size and current_chunk:
                chunks.append(" ".join(current_chunk))
                current_chunk = [word]
                current_length = word_len
            else:
                current_chunk.append(word)
                current_length += word_len
        
        if current_chunk:
            chunks.append(" ".join(current_chunk))
        
        return chunks
