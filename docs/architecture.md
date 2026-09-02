# Architecture Overview

## System Components

```
┌─────────────────────────────────────────────────────────────────┐
│                       Frontend (React)                          │
│                      (React + Vite)                            │
└──────────────────────────────┬──────────────────────────────────┘
                               │
                               │ REST API
                               ▼
┌─────────────────────────────────────────────────────────────────┐
│                     FastAPI Backend                             │
│ ┌──────────────────────────────────────────────────────────────┐│
│ │  Routers:  /analytics   /ml   /agent   /reports  /data-quality ││
│ ├──────────────────────────────────────────────────────────────┤│
│ │  LLM Abstraction Layer                                       ││
│ │  ├─ BaseLLMProvider                                          ││
│ │  ├─ OpenAIProvider (active)                                  ││
│ │  └─ AnthropicProvider (active)                               ││
│ ├──────────────────────────────────────────────────────────────┤│
│ │  Agent Workflow                                              ││
│ │  ├─ Planner (intent classification)                          ││
│ │  ├─ Executor (tool invocation)                               ││
│ │  └─ Verifier (evidence validation)                           ││
│ ├──────────────────────────────────────────────────────────────┤│
│ │  Tools                                                        ││
│ │  ├─ Analytics: KPIs, rep performance, pipeline               ││
│ │  ├─ Metrics: definitions, registry, validation               ││
│ │  ├─ ML: forecasts, deal scores, clustering                   ││
│ │  ├─ RAG: knowledge base retrieval                            ││
│ │  └─ Reports: markdown generation                             ││
│ ├──────────────────────────────────────────────────────────────┤│
│ │  Data Layers                                                  ││
│ │  ├─ Ingestion (CSV loaders, source registry)                 ││
│ │  ├─ Validation (quality checks, rule engine)                 ││
│ │  ├─ Transformations (canonical field mapping, registry)         ││
│ │  ├─ Metrics (definitions, registry)                          ││
│ │  ├─ Statistics (analysis, anomaly detection)                 ││
│ │  └─ ML (models, evaluation, safety)                          ││
│ ├──────────────────────────────────────────────────────────────┤│
│ │  Database: PostgreSQL + SQLAlchemy ORM                       ││
│ └──────────────────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────────────┘
```

## Data Flow

```
Raw Data (CSV/API)
    ↓
Ingestion → CSVLoader, SourceRegistry
    ↓
Validation → DataQualityValidator, QualityReport
    ↓
Transformations → Feature engineering, canonicalization (canonical_mapping.py)
    ↓
Metrics Registry → Governed definitions, formulas
    ↓
Analytics Gold Layer → KPIs, statistics, analysis
    ↓
Agent/Reports/ML ← Consume refined data
```

## Request Flow: User Chat

```
1. User submits message via /agent/chat
2. Planner classifies intent
3. Executor calls appropriate tools
4. Tools query database or metrics registry
5. Verifier checks evidence completeness
6. LLM provider generates response (with tools context)
    - If LLM is unavailable, deterministic evidence-backed fallback is returned
7. Response returned with:
   - answer: AI-generated response
   - intent: classified intent
   - tools_used: list of tools called
   - evidence_summary: key data points
   - warnings: data limitations or caveats
```

## Key Design Decisions

### 1. LLM Provider Abstraction
- Supports multiple providers (OpenAI default, Anthropic, custom); both ship as real
  implementations, selected by `LLM_PROVIDER`, not one active provider and one stub
- Configuration via `LLM_PROVIDER` env var
- Clean separation of LLM logic from routing

### 2. Tool-Based Agent
- Agent uses tools to gather evidence before LLM call
- Tools return structured data (not raw SQL results)
- Prevents hallucination by grounding in real data
- Extensible: easy to add new tools

### 3. Metrics Registry
- Single source of truth for metric definitions
- Governs metric calculations across platform
- Provides caveats and formulas to users
- Agent can validate metric requests before tool calls

### 4. Data Lifecycle Stages
- Bronze (Raw): Minimal processing, full audit trail
- Silver (Validated): Quality checks, business rules
- Gold (Analytics): Aggregated, ready for consumption
- ML/Agent/Reports: Consume refined data

### 5. Modular Folder Structure
- Clear separation of concerns
- Each module can be developed and tested independently
- Easy to add new ingestion types, tools, analysis functions

## Future Enhancements

1. **Vector DB for RAG**: Replace TF-IDF with pgvector or Pinecone
2. **dbt Transformations**: Codify Silver/Gold layer logic
3. **Time-Series Backtest**: Expand current rolling-origin backtest coverage and diagnostics
4. **Advanced RAG**: Hybrid retrieval with semantic search
5. **Streaming**: Real-time deal/activity ingestion
6. **ML Monitoring**: Track model drift and retraining triggers
7. **Agent Memory**: Persistent conversation context
8. **Custom Tools**: Plugin system for user-defined tools
