# Agent Design

## Runtime Flow

`/agent/chat` follows a strict evidence-first workflow:

1. IntentPlanner classifies the user question.
2. ToolExecutor calls intent-specific tools (metrics, analytics, ML, RAG, reports).
3. EvidenceVerifier validates that useful evidence exists.
4. If evidence is insufficient, the route returns:
   - `reply`: `Insufficient data available to answer this confidently.`
5. If evidence is sufficient, LLM response generation is attempted with tool evidence.
6. If LLM provider is unavailable, a deterministic evidence-backed fallback summary is returned.

## Intents

- `metric_question`
- `rep_performance`
- `forecast_question`
- `anomaly_question`
- `report_request`
- `definition_question`
- `general_sales_question`
- `unknown`

## Tool Output Contract

All agent tools return structured evidence:

```json
{
  "tool_name": "...",
  "status": "success|warning|error",
  "data": {},
  "warnings": [],
  "sources": []
}
```

## Safety Rules

- Agent prompt explicitly instructs: use only provided evidence.
- Agent does not invent numbers when evidence is missing.
- Unknown metrics are returned as clear structured errors.
- Warnings and caveats from metrics/tools are propagated to API response.
- Deterministic fallback explicitly labels non-LLM mode and avoids fabricated numbers.

## API Response

`/agent/chat` returns:

- `reply`
- `intent`
- `tools_used`
- `evidence_summary`
- `warnings`

`reply` remains backward-compatible with the existing frontend chat contract.
