"""
AI Agent endpoint with planner -> executor -> verifier workflow.
"""
from __future__ import annotations

import json
from typing import Any, AsyncGenerator, Optional

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from backend.agent.executor import ToolExecutor
from backend.agent.answer_quality import compute_answer_quality
from backend.agent.chart_payloads import build_chart_payloads
from backend.agent.fallback_response import build_deterministic_response
from backend.agent.planner import IntentPlanner
from backend.agent.prompts import AGENT_SYSTEM_PROMPT
from backend.agent.tools.ml_tools import get_deal_risk_summary, get_forecast_summary, get_rep_clusters_summary
from backend.agent.verifier import EvidenceVerifier
from backend.database import get_db
from backend.auth.dependencies import require_permission
from backend.auth.tenant import get_tenant_context
from backend.llm import get_llm_provider
from backend.rag.rag_service import get_rag_service
from backend.config import settings

router = APIRouter(
    prefix="/agent",
    tags=["AI Agent"],
    dependencies=[Depends(require_permission("run_agent_workflow")), Depends(get_tenant_context)],
)

planner = IntentPlanner()
executor = ToolExecutor()
verifier = EvidenceVerifier()


class ChatMessage(BaseModel):
    role: str
    content: str


class AgentRequest(BaseModel):
    message: str
    history: list[ChatMessage] = []


class AgentResponse(BaseModel):
    reply: str
    answer: str
    intent: str
    tools_used: list[str]
    evidence_summary: dict[str, Any]
    evidence: list[dict[str, Any]] = []
    assumptions: list[str] = []
    warnings: list[str]
    confidence: str = "medium"
    recommended_next_action: str = "Review supporting evidence before taking action."
    requires_confirmation: bool = False
    charts: list[dict[str, Any]] = []
    answer_quality: dict[str, Any] = {}


@router.get("/ml-evidence")
async def ml_evidence_snapshot(db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    forecast = await get_forecast_summary(db)
    deal_risk = await get_deal_risk_summary(db)
    clusters = await get_rep_clusters_summary(db)

    warnings: list[str] = []
    warnings.extend(forecast.get("warnings", []))
    warnings.extend(deal_risk.get("warnings", []))
    warnings.extend(clusters.get("warnings", []))

    risk_data = deal_risk.get("data", {}) if isinstance(deal_risk.get("data"), dict) else {}
    cluster_rows = clusters.get("data", {}).get("clusters", []) if isinstance(clusters.get("data"), dict) else []
    forecast_data = forecast.get("data", {}) if isinstance(forecast.get("data"), dict) else {}

    return {
        "forecast": forecast_data,
        "deal_risk": risk_data,
        "rep_clusters": {"clusters": cluster_rows, "cluster_count": len(cluster_rows)},
        "tools_used": ["get_forecast_summary", "get_deal_risk_summary", "get_rep_clusters_summary"],
        "warnings": sorted(set(warnings)),
    }


def _format_evidence_summary(evidence_results: list[dict[str, Any]]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for result in evidence_results:
        summary[result.get("tool_name", "unknown")] = {
            "status": result.get("status"),
            "sources": result.get("sources", []),
            "warnings": result.get("warnings", []),
            "has_data": bool(result.get("data")),
        }
    return summary


def _compute_answer_quality(
    *,
    intent: str,
    tools_used: list[str],
    evidence_results: list[dict[str, Any]],
    warnings: list[str],
    verified: bool,
    reply: str,
    used_rag: bool = False,
) -> dict[str, Any]:
    return compute_answer_quality(
        intent=intent,
        tools_used=tools_used,
        evidence_results=evidence_results,
        warnings=warnings,
        verified=verified,
        reply=reply,
        used_rag=used_rag,
    )


def _confidence_from_quality(answer_quality: dict[str, Any]) -> str:
    score = float(answer_quality.get("score") or 0.0)
    if score >= 0.8:
        return "high"
    if score >= 0.5:
        return "medium"
    return "low"


def _derive_assumptions(warnings: list[str], verified: bool) -> list[str]:
    assumptions: list[str] = []
    if not verified:
        assumptions.append("Evidence coverage is partial; verify data before operational decisions.")
    if any("fallback" in (w or "").lower() for w in warnings):
        assumptions.append("At least one response section used fallback logic.")
    if any("rag" in (w or "").lower() for w in warnings):
        assumptions.append("Knowledge-base context may be static and not live operational data.")
    if not assumptions:
        assumptions.append("Metrics reflect currently available tool and database evidence.")
    return assumptions


def _recommended_action(intent: str, warnings: list[str], requires_confirmation: bool) -> str:
    if requires_confirmation:
        return "Obtain explicit human approval and include intent scope before any write-side action."
    if any("insufficient" in (w or "").lower() for w in warnings):
        return "Refresh or ingest missing data, then rerun the question for a higher-confidence answer."

    mapping = {
        "forecast_question": "Review forecast confidence bands and compare against current pipeline coverage.",
        "payout_request": "Open payout trace details and validate rule/source-record lineage before approval.",
        "pipeline_rescue_whatif": "Execute top-3 deal rescue actions and track weekly lift versus baseline coverage.",
        "report_request": "Share the generated report with stakeholders and capture decision follow-ups.",
    }
    return mapping.get(intent, "Validate evidence, then execute the next RevOps action with owner and due date.")


_SENSITIVE_ACTION_KEYWORDS = {
    "approve payout": "payout approval",
    "payout approval": "payout approval",
    "lock payout": "payout lock",
    "change rule": "comp rule change",
    "update rule": "comp rule change",
    "retrain model": "model retraining",
    "run training": "model retraining",
    "reload data": "data reload",
    "reingest": "data reload",
    "switch company": "tenant switch",
}


def _detect_sensitive_action(message: str) -> str | None:
    text = (message or "").lower()
    for phrase, label in _SENSITIVE_ACTION_KEYWORDS.items():
        if phrase in text:
            return label
    return None


def _has_explicit_approval(message: str) -> bool:
    text = (message or "").lower()
    tokens = [
        "i approve",
        "approved",
        "explicit approval",
        "approved action",
        "confirm write action",
    ]
    return any(token in text for token in tokens)


def _build_agent_response(
    *,
    reply: str,
    intent: str,
    tools_used: list[str],
    evidence_results: list[dict[str, Any]],
    evidence_summary: dict[str, Any],
    warnings: list[str],
    charts: list[dict[str, Any]],
    answer_quality: dict[str, Any],
    verified: bool,
    requires_confirmation: bool = False,
) -> AgentResponse:
    final_warnings = sorted(set(warnings))
    return AgentResponse(
        reply=reply,
        answer=reply,
        intent=intent,
        tools_used=tools_used,
        evidence_summary=evidence_summary,
        evidence=evidence_results,
        assumptions=_derive_assumptions(final_warnings, verified=verified),
        warnings=final_warnings,
        confidence=_confidence_from_quality(answer_quality),
        recommended_next_action=_recommended_action(intent, final_warnings, requires_confirmation),
        requires_confirmation=requires_confirmation,
        charts=charts,
        answer_quality=answer_quality,
    )


@router.post("/chat", response_model=AgentResponse)
async def agent_chat(req: AgentRequest, db: AsyncSession = Depends(get_db)):
    sensitive_action = _detect_sensitive_action(req.message)
    if sensitive_action and not _has_explicit_approval(req.message):
        guardrail_reply = (
            f"I can help with {sensitive_action}, but I will not execute or recommend write-side changes without explicit approval. "
            "Please restate the request with explicit approval context and intended scope."
        )
        guardrail_warnings = [
            "Sensitive action guardrail triggered.",
            "No write-capable action was executed.",
        ]
        quality = _compute_answer_quality(
            intent="sensitive_action_guardrail",
            tools_used=[],
            evidence_results=[],
            warnings=guardrail_warnings,
            verified=False,
            reply=guardrail_reply,
        )
        return _build_agent_response(
            reply=guardrail_reply,
            intent="sensitive_action_guardrail",
            tools_used=[],
            evidence_results=[],
            evidence_summary={},
            warnings=guardrail_warnings,
            charts=[],
            answer_quality=quality,
            verified=False,
            requires_confirmation=True,
        )

    state = planner.plan(req.message)
    state = await executor.execute_for_intent(state, db_session=db)
    charts = build_chart_payloads(state.intent or "unknown", state.evidence_results)
    verified, warnings = verifier.verify_state(state)

    if not verified:
        # Before giving up: try RAG to answer definition/methodology questions
        rag_context = ""
        rag_guardrail_warnings: list[str] = []
        try:
            rag_svc = get_rag_service()
            rag_chunks = rag_svc.retrieve_context_annotated(req.message, top_k=4)
            safe_chunks = [chunk for chunk in rag_chunks if not chunk.get("has_numeric_claims")]
            filtered_count = len(rag_chunks) - len(safe_chunks)
            if filtered_count:
                rag_guardrail_warnings.append(
                    "RAG guardrail filtered unverified numeric claims; live values must come from DB tools."
                )
            if safe_chunks:
                rag_context = rag_svc.format_retrieved_context(safe_chunks)
        except Exception:
            rag_context = ""

        if rag_context:
            # Return RAG answer with clear sourcing — do NOT use for live metrics
            reply = (
                f"Based on knowledge base definitions and methodology:\n\n{rag_context}\n\n"
                "⚠️ Note: The above is from static knowledge base documents. "
                "For live company metrics, please ask a more specific metric question."
            )
            final_warnings = warnings + rag_guardrail_warnings + ["Answered via RAG fallback — no live tool data available"]
            quality = _compute_answer_quality(
                intent=state.intent or "unknown",
                tools_used=state.tools_called + ["rag_retrieval"],
                evidence_results=state.evidence_results,
                warnings=final_warnings,
                verified=verified,
                reply=reply,
                used_rag=True,
            )
            return _build_agent_response(
                reply=reply,
                intent=state.intent or "unknown",
                tools_used=state.tools_called + ["rag_retrieval"],
                evidence_summary=_format_evidence_summary(state.evidence_results),
                evidence_results=state.evidence_results,
                warnings=final_warnings,
                charts=charts,
                answer_quality=quality,
                verified=verified,
            )

        fallback_reply = build_deterministic_response(
            intent=state.intent or "unknown",
            evidence=state.evidence_results,
            tools_used=state.tools_called,
            warnings=warnings,
        )
        quality = _compute_answer_quality(
            intent=state.intent or "unknown",
            tools_used=state.tools_called,
            evidence_results=state.evidence_results,
            warnings=warnings,
            verified=verified,
            reply=fallback_reply,
        )
        return _build_agent_response(
            reply=fallback_reply,
            intent=state.intent or "unknown",
            tools_used=state.tools_called,
            evidence_summary=_format_evidence_summary(state.evidence_results),
            evidence_results=state.evidence_results,
            warnings=warnings,
            charts=charts,
            answer_quality=quality,
            verified=verified,
        )

    # For summary/report intents, prefer deterministic report markdown output
    # so business users always get a concrete answer even when external LLM
    # providers are unavailable or produce weak narrative.
    if (state.intent or "") == "report_request":
        report_item = next(
            (
                item
                for item in state.evidence_results
                if str(item.get("tool_name", "")).startswith("generate_")
            ),
            None,
        )
        report_data = report_item.get("data", {}) if isinstance(report_item, dict) else {}
        markdown = report_data.get("markdown", "") if isinstance(report_data, dict) else ""
        if markdown:
            final_warnings = sorted(set(warnings + ["Returned deterministic report summary."]))
            quality = _compute_answer_quality(
                intent=state.intent or "unknown",
                tools_used=state.tools_called,
                evidence_results=state.evidence_results,
                warnings=final_warnings,
                verified=verified,
                reply=markdown,
            )
            return _build_agent_response(
                reply=markdown,
                intent=state.intent or "unknown",
                tools_used=state.tools_called,
                evidence_summary=_format_evidence_summary(state.evidence_results),
                evidence_results=state.evidence_results,
                warnings=final_warnings,
                charts=charts,
                answer_quality=quality,
                verified=verified,
            )

    if (state.intent or "") in {
        "rep_quota_whatif",
        "pipeline_rescue_whatif",
        "plan_performance_question",
        "forecast_question",
        "business_diagnostic_question",
        "pipeline_coverage_check",
        "quota_risk",
    }:
        deterministic = build_deterministic_response(
            intent=state.intent or "unknown",
            evidence=state.evidence_results,
            tools_used=state.tools_called,
            warnings=warnings,
        )
        if deterministic and "Insufficient data" not in deterministic:
            intent_name = state.intent or "unknown"
            final_warnings = sorted(set(warnings + [f"Returned deterministic {intent_name} summary."]))
            quality = _compute_answer_quality(
                intent=intent_name,
                tools_used=state.tools_called,
                evidence_results=state.evidence_results,
                warnings=final_warnings,
                verified=verified,
                reply=deterministic,
            )
            return _build_agent_response(
                reply=deterministic,
                intent=intent_name,
                tools_used=state.tools_called,
                evidence_summary=_format_evidence_summary(state.evidence_results),
                evidence_results=state.evidence_results,
                warnings=final_warnings,
                charts=charts,
                answer_quality=quality,
                verified=verified,
            )

    llm_reply: Optional[str] = None
    try:
        llm_provider = get_llm_provider(
            provider_name=settings.LLM_PROVIDER,
            openai_key=settings.OPENAI_API_KEY,
            anthropic_key=settings.ANTHROPIC_API_KEY,
        )
        evidence_json = json.dumps(state.evidence_results, default=str)
        messages = [{"role": m.role, "content": m.content} for m in req.history]
        messages.append({"role": "user", "content": req.message})

        llm_reply = await llm_provider.chat_completion(
            messages=messages,
            system_prompt=f"{AGENT_SYSTEM_PROMPT}\n\nEVIDENCE:\n{evidence_json}",
            temperature=0.2,
            max_tokens=700,
        )
    except Exception as exc:
        llm_reply = build_deterministic_response(
            intent=state.intent or "unknown",
            evidence=state.evidence_results,
            tools_used=state.tools_called,
            warnings=warnings,
        )
        warnings.append(f"LLM provider unavailable ({type(exc).__name__}): {str(exc)}")
        warnings.append("Returned safe deterministic fallback response")

    if not llm_reply:
        llm_reply = build_deterministic_response(
            intent=state.intent or "unknown",
            evidence=state.evidence_results,
            tools_used=state.tools_called,
            warnings=warnings,
        )

    final_warnings = sorted(set(warnings))
    quality = _compute_answer_quality(
        intent=state.intent or "unknown",
        tools_used=state.tools_called,
        evidence_results=state.evidence_results,
        warnings=final_warnings,
        verified=verified,
        reply=llm_reply,
    )

    return _build_agent_response(
        reply=llm_reply,
        intent=state.intent or "unknown",
        tools_used=state.tools_called,
        evidence_summary=_format_evidence_summary(state.evidence_results),
        evidence_results=state.evidence_results,
        warnings=final_warnings,
        charts=charts,
        answer_quality=quality,
        verified=verified,
    )


@router.post("/chat/stream")
async def agent_chat_stream(req: AgentRequest, db: AsyncSession = Depends(get_db)):
    """
    SSE streaming variant of /agent/chat.

    Runs planner + executor synchronously (tool results buffered),
    then streams the LLM narrative token-by-token.

    Event format: data: {"delta": "token", "done": false}
    Final event:  data: {"delta": "", "done": true, "full_response": "...", "tool_calls": [...]}
    """
    sensitive_action = _detect_sensitive_action(req.message)
    if sensitive_action and not _has_explicit_approval(req.message):
        guardrail_reply = (
            f"I can help with {sensitive_action}, but explicit approval is required before any write-side operation. "
            "No write action was executed."
        )
        guardrail_warnings = ["Sensitive action guardrail triggered.", "No write-capable action was executed."]
        quality = _compute_answer_quality(
            intent="sensitive_action_guardrail",
            tools_used=[],
            evidence_results=[],
            warnings=guardrail_warnings,
            verified=False,
            reply=guardrail_reply,
        )

        async def _guardrail_stream() -> AsyncGenerator[str, None]:
            for word in guardrail_reply.split(" "):
                payload = json.dumps({"delta": word + " ", "done": False})
                yield f"data: {payload}\n\n"
            final = json.dumps({
                "delta": "",
                "done": True,
                "full_response": guardrail_reply,
                "tool_calls": [],
                "intent": "sensitive_action_guardrail",
                "warnings": sorted(set(guardrail_warnings)),
                "charts": [],
                "answer_quality": quality,
                "assumptions": _derive_assumptions(guardrail_warnings, verified=False),
                "confidence": _confidence_from_quality(quality),
                "recommended_next_action": _recommended_action("sensitive_action_guardrail", guardrail_warnings, True),
                "requires_confirmation": True,
            })
            yield f"data: {final}\n\n"

        return StreamingResponse(_guardrail_stream(), media_type="text/event-stream")

    state = planner.plan(req.message)
    state = await executor.execute_for_intent(state, db_session=db)
    charts = build_chart_payloads(state.intent or "unknown", state.evidence_results)
    verified, warnings = verifier.verify_state(state)

    if not verified:
        fallback = build_deterministic_response(
            intent=state.intent or "unknown",
            evidence=state.evidence_results,
            tools_used=state.tools_called,
            warnings=warnings,
        )
        quality = _compute_answer_quality(
            intent=state.intent or "unknown",
            tools_used=state.tools_called,
            evidence_results=state.evidence_results,
            warnings=warnings,
            verified=verified,
            reply=fallback,
        )
        async def _fallback_stream() -> AsyncGenerator[str, None]:
            for word in fallback.split(" "):
                payload = json.dumps({"delta": word + " ", "done": False})
                yield f"data: {payload}\n\n"
            final = json.dumps({
                "delta": "",
                "done": True,
                "full_response": fallback,
                "tool_calls": state.tools_called,
                "intent": state.intent or "unknown",
                "warnings": sorted(set(warnings)),
                "charts": charts,
                "answer_quality": quality,
                "assumptions": _derive_assumptions(warnings, verified=verified),
                "confidence": _confidence_from_quality(quality),
                "recommended_next_action": _recommended_action(state.intent or "unknown", warnings, False),
                "requires_confirmation": False,
            })
            yield f"data: {final}\n\n"
        return StreamingResponse(_fallback_stream(), media_type="text/event-stream")

    if (state.intent or "") == "report_request":
        report_item = next(
            (
                item
                for item in state.evidence_results
                if str(item.get("tool_name", "")).startswith("generate_")
            ),
            None,
        )
        report_data = report_item.get("data", {}) if isinstance(report_item, dict) else {}
        markdown = report_data.get("markdown", "") if isinstance(report_data, dict) else ""
        if markdown:
            final_warnings = sorted(set(warnings + ["Returned deterministic report summary."]))
            quality = _compute_answer_quality(
                intent=state.intent or "unknown",
                tools_used=state.tools_called,
                evidence_results=state.evidence_results,
                warnings=final_warnings,
                verified=verified,
                reply=markdown,
            )
            async def _report_stream() -> AsyncGenerator[str, None]:
                for word in markdown.split(" "):
                    payload = json.dumps({"delta": word + " ", "done": False})
                    yield f"data: {payload}\n\n"
                final = json.dumps({
                    "delta": "",
                    "done": True,
                    "full_response": markdown,
                    "tool_calls": state.tools_called,
                    "intent": state.intent or "unknown",
                    "warnings": final_warnings,
                    "charts": charts,
                    "answer_quality": quality,
                    "assumptions": _derive_assumptions(final_warnings, verified=verified),
                    "confidence": _confidence_from_quality(quality),
                    "recommended_next_action": _recommended_action(state.intent or "unknown", final_warnings, False),
                    "requires_confirmation": False,
                })
                yield f"data: {final}\n\n"

            return StreamingResponse(_report_stream(), media_type="text/event-stream")

    if (state.intent or "") in {
        "rep_quota_whatif",
        "pipeline_rescue_whatif",
        "plan_performance_question",
        "forecast_question",
        "business_diagnostic_question",
        "pipeline_coverage_check",
        "quota_risk",
    }:
        deterministic = build_deterministic_response(
            intent=state.intent or "unknown",
            evidence=state.evidence_results,
            tools_used=state.tools_called,
            warnings=warnings,
        )
        if deterministic and "Insufficient data" not in deterministic:
            final_warnings = sorted(set(warnings + [f"Returned deterministic {state.intent or 'unknown'} summary."]))
            quality = _compute_answer_quality(
                intent=state.intent or "unknown",
                tools_used=state.tools_called,
                evidence_results=state.evidence_results,
                warnings=final_warnings,
                verified=verified,
                reply=deterministic,
            )
            async def _deterministic_stream() -> AsyncGenerator[str, None]:
                for word in deterministic.split(" "):
                    payload = json.dumps({"delta": word + " ", "done": False})
                    yield f"data: {payload}\n\n"
                final = json.dumps({
                    "delta": "",
                    "done": True,
                    "full_response": deterministic,
                    "tool_calls": state.tools_called,
                    "intent": state.intent or "unknown",
                    "warnings": final_warnings,
                    "charts": charts,
                    "answer_quality": quality,
                    "assumptions": _derive_assumptions(final_warnings, verified=verified),
                    "confidence": _confidence_from_quality(quality),
                    "recommended_next_action": _recommended_action(state.intent or "unknown", final_warnings, False),
                    "requires_confirmation": False,
                })
                yield f"data: {final}\n\n"

            return StreamingResponse(_deterministic_stream(), media_type="text/event-stream")

    async def _llm_stream() -> AsyncGenerator[str, None]:
        full_response_parts: list[str] = []
        try:
            llm_provider = get_llm_provider(
                provider_name=settings.LLM_PROVIDER,
                openai_key=settings.OPENAI_API_KEY,
                anthropic_key=settings.ANTHROPIC_API_KEY,
            )
            evidence_json = json.dumps(state.evidence_results, default=str)
            messages = [{"role": m.role, "content": m.content} for m in req.history]
            messages.append({"role": "user", "content": req.message})

            async for token in llm_provider.stream_complete(
                messages=messages,
                system_prompt=f"{AGENT_SYSTEM_PROMPT}\n\nEVIDENCE:\n{evidence_json}",
                temperature=0.2,
                max_tokens=700,
            ):
                full_response_parts.append(token)
                payload = json.dumps({"delta": token, "done": False})
                yield f"data: {payload}\n\n"

        except Exception as exc:
            fallback = build_deterministic_response(
                intent=state.intent or "unknown",
                evidence=state.evidence_results,
                tools_used=state.tools_called,
                warnings=warnings + [f"LLM stream failed: {exc}"],
            )
            for word in fallback.split(" "):
                token = word + " "
                full_response_parts.append(token)
                payload = json.dumps({"delta": token, "done": False})
                yield f"data: {payload}\n\n"

        full_response = "".join(full_response_parts)
        final_warnings = sorted(set(warnings))
        quality = _compute_answer_quality(
            intent=state.intent or "unknown",
            tools_used=state.tools_called,
            evidence_results=state.evidence_results,
            warnings=final_warnings,
            verified=verified,
            reply=full_response,
        )
        final = json.dumps({
            "delta": "",
            "done": True,
            "full_response": full_response,
            "tool_calls": state.tools_called,
            "intent": state.intent or "unknown",
            "warnings": final_warnings,
            "charts": charts,
            "answer_quality": quality,
            "assumptions": _derive_assumptions(final_warnings, verified=verified),
            "confidence": _confidence_from_quality(quality),
            "recommended_next_action": _recommended_action(state.intent or "unknown", final_warnings, False),
            "requires_confirmation": False,
        })
        yield f"data: {final}\n\n"

    return StreamingResponse(_llm_stream(), media_type="text/event-stream")


# ── Workflow endpoint ─────────────────────────────────────────────────────

class WorkflowRequest(BaseModel):
    period: Optional[str] = None
    skip_steps: list[str] = []
    only_steps: list[str] = []


@router.post("/workflows/sales-performance")
async def run_sales_performance_workflow(
    req: WorkflowRequest,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """
    Run the full 10-step sales performance analysis pipeline.

    Returns a structured result with per-step status and a summary.
    All numerical data is sourced from the database/ML models — no invented values.
    Steps that fail are marked status='failed' but do not abort the pipeline.
    """
    from backend.agent.workflows.sales_performance_pipeline import run_sales_performance_pipeline
    return await run_sales_performance_pipeline(
        db=db,
        period=req.period or None,
        options={
            "skip_steps": req.skip_steps,
            "only_steps": req.only_steps,
        },
    )

