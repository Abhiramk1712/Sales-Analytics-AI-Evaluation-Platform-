# RAG Design

## Knowledge Base

The local knowledge base is markdown-first and stored in `docs/knowledge_base/`.

Primary documents:
- `metric_definitions.md`
- `sales_glossary.md`
- `forecasting_assumptions.md`
- `reporting_templates.md`

## Retrieval Pipeline

1. `DocumentLoader` loads markdown files.
2. `DocumentChunker` splits content into heading-aware chunks.
3. `Retriever` ranks chunks with TF-IDF, with keyword fallback.
4. `RAGService.retrieve_context(query, top_k)` normalizes output fields:
   - `content`
   - `source_document`
   - `score`
   - `heading`

Agent RAG tools use `source_document` as the canonical source key when emitting evidence citations.

## Agent Integration

RAG is used primarily for:
- `definition_question`
- `general_sales_question`
- `report_request`

The agent surfaces retrieved source documents via evidence summary so users can trace knowledge citations.

## Safety

- Empty retrieval query is handled safely and returns warning state.
- No external paid vector DB is required in current implementation.
- Retrieval failures fall back to keyword matching when possible.

## Future Improvements

- Hybrid lexical + semantic retrieval.
- Embeddings-backed indexing (for example pgvector) while preserving local markdown source-of-truth.
