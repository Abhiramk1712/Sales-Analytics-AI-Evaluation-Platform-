from __future__ import annotations

from datetime import datetime, timezone
import re
from typing import Any


def _clamp(value: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, value))


def _parse_iso_datetime(value: str) -> datetime | None:
    text = (value or "").strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            return datetime.fromisoformat(text.replace("Z", "+00:00"))
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _quarter_end_date(year: int, quarter: int) -> datetime:
    end_month = quarter * 3
    if end_month in (1, 3, 5, 7, 8, 10, 12):
        end_day = 31
    elif end_month == 2:
        leap = (year % 400 == 0) or (year % 4 == 0 and year % 100 != 0)
        end_day = 29 if leap else 28
    else:
        end_day = 30
    return datetime(year, end_month, end_day, tzinfo=timezone.utc)


def _parse_period_end(value: str) -> datetime | None:
    text = (value or "").strip()
    month_match = re.match(r"^(\d{4})-(\d{2})(?:-\d{2})?$", text)
    if month_match:
        year = int(month_match.group(1))
        month = int(month_match.group(2))
        if month < 1 or month > 12:
            return None
        if month in (1, 3, 5, 7, 8, 10, 12):
            day = 31
        elif month == 2:
            leap = (year % 400 == 0) or (year % 4 == 0 and year % 100 != 0)
            day = 29 if leap else 28
        else:
            day = 30
        return datetime(year, month, day, tzinfo=timezone.utc)

    quarter_match = re.match(r"^(\d{4})-Q([1-4])$", text, flags=re.IGNORECASE)
    if quarter_match:
        year = int(quarter_match.group(1))
        quarter = int(quarter_match.group(2))
        return _quarter_end_date(year, quarter)

    year_match = re.match(r"^(\d{4})$", text)
    if year_match:
        year = int(year_match.group(1))
        return datetime(year, 12, 31, tzinfo=timezone.utc)

    return None


def _collect_recency_datetimes(evidence_results: list[dict[str, Any]]) -> list[datetime]:
    candidates: list[datetime] = []

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            for key, inner in value.items():
                lower = str(key).lower()
                if lower in {
                    "predicted_at",
                    "generated_at",
                    "created_at",
                    "updated_at",
                    "recorded_at",
                    "last_updated",
                    "last_run_at",
                    "trained_at",
                }:
                    if isinstance(inner, str):
                        parsed = _parse_iso_datetime(inner)
                        if parsed is not None:
                            if parsed.tzinfo is None:
                                parsed = parsed.replace(tzinfo=timezone.utc)
                            candidates.append(parsed.astimezone(timezone.utc))
                elif lower in {"period", "latest_period", "period_used"} and isinstance(inner, str):
                    period_dt = _parse_period_end(inner)
                    if period_dt is not None:
                        candidates.append(period_dt)
                walk(inner)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(evidence_results)
    return candidates


def _freshness_score(evidence_results: list[dict[str, Any]]) -> tuple[int, dict[str, Any]]:
    candidates = _collect_recency_datetimes(evidence_results)
    if not candidates:
        return 45, {
            "latest_timestamp": None,
            "age_days": None,
            "note": "No explicit recency timestamp found in evidence.",
        }

    latest = max(candidates)
    now = datetime.now(timezone.utc)
    age_days = max(0, int((now - latest).total_seconds() // 86400))

    if age_days <= 3:
        score = 100
    elif age_days <= 14:
        score = 92
    elif age_days <= 30:
        score = 84
    elif age_days <= 60:
        score = 72
    elif age_days <= 90:
        score = 62
    elif age_days <= 180:
        score = 50
    elif age_days <= 365:
        score = 35
    else:
        score = 20

    return score, {
        "latest_timestamp": latest.isoformat(),
        "age_days": age_days,
    }


def _coverage_score(evidence_results: list[dict[str, Any]], tools_used: list[str]) -> tuple[int, dict[str, Any]]:
    tool_count = len(tools_used)
    if tool_count == 0:
        return 0, {
            "tools_used": 0,
            "with_data": 0,
            "source_count": 0,
            "status_breakdown": {"success": 0, "warning": 0, "error": 0},
        }

    success = 0
    warning = 0
    error = 0
    with_data = 0
    source_set: set[str] = set()

    for result in evidence_results:
        status = str(result.get("status") or "").lower()
        if status == "success":
            success += 1
        elif status == "warning":
            warning += 1
        elif status == "error":
            error += 1

        data = result.get("data")
        has_data = False
        if isinstance(data, dict):
            has_data = len(data) > 0
        elif isinstance(data, list):
            has_data = len(data) > 0
        elif isinstance(data, (str, int, float)):
            has_data = data not in ("", 0, 0.0)
        if has_data:
            with_data += 1

        sources = result.get("sources")
        if isinstance(sources, list):
            for source in sources:
                src = str(source or "").strip().lower()
                if src:
                    source_set.add(src)

    data_ratio = with_data / max(tool_count, 1)
    tool_factor = min(1.0, tool_count / 5.0)
    status_ratio = ((success * 1.0) + (warning * 0.55) + (error * 0.2)) / max(tool_count, 1)
    source_diversity = min(1.0, len(source_set) / 4.0)

    score = 100.0 * ((0.25 * tool_factor) + (0.30 * data_ratio) + (0.25 * status_ratio) + (0.20 * source_diversity))
    score = _clamp(score)

    return int(round(score)), {
        "tools_used": tool_count,
        "with_data": with_data,
        "source_count": len(source_set),
        "status_breakdown": {
            "success": success,
            "warning": warning,
            "error": error,
        },
    }


def _confidence_score(
    verified: bool,
    warnings: list[str],
    evidence_results: list[dict[str, Any]],
    reply: str,
    used_rag: bool,
) -> tuple[int, dict[str, Any]]:
    warning_count = len(warnings)
    error_count = sum(1 for r in evidence_results if str(r.get("status") or "").lower() == "error")

    score = 90.0 if verified else 42.0

    lower_warnings = " ".join(warnings).lower()
    severe_tokens = [
        "insufficient",
        "not found",
        "no persisted",
        "missing",
        "failed",
        "error",
        "unavailable",
    ]
    medium_tokens = [
        "inferred",
        "estimated",
        "assumed",
        "approx",
        "fallback",
        "stale",
    ]

    severe_hits = sum(1 for token in severe_tokens if token in lower_warnings)
    medium_hits = sum(1 for token in medium_tokens if token in lower_warnings)

    warning_penalty = min(45.0, (warning_count * 4.0) + (severe_hits * 4.0) + (medium_hits * 2.0))
    score -= warning_penalty
    score -= error_count * 10.0

    if verified and warning_count == 0 and error_count == 0:
        score += 5.0

    if "insufficient data available" in (reply or "").lower():
        score = min(score, 35.0)

    if used_rag:
        score = min(score, 58.0)

    score = _clamp(score, lo=5.0, hi=100.0)
    return int(round(score)), {
        "verified": verified,
        "warning_count": warning_count,
        "warning_penalty": round(warning_penalty, 2),
        "severe_warning_signals": severe_hits,
        "medium_warning_signals": medium_hits,
        "error_tool_count": error_count,
        "used_rag": used_rag,
    }


def _quality_level(score: int) -> str:
    if score >= 82:
        return "high"
    if score >= 58:
        return "medium"
    return "low"


def _summary(level: str, coverage: int, confidence: int, freshness: int) -> str:
    if level == "high":
        return "High trust: strong evidence coverage with reliable confidence signals."
    if level == "medium":
        if freshness < 55:
            return "Medium trust: evidence is solid, but data freshness limits confidence."
        if confidence < 58:
            return "Medium trust: evidence exists, but warnings reduce confidence."
        return "Medium trust: useful answer with some caveats in confidence or coverage."
    return "Low trust: limited or noisy evidence; treat this answer as directional."


def compute_answer_quality(
    *,
    intent: str,
    tools_used: list[str],
    evidence_results: list[dict[str, Any]],
    warnings: list[str],
    verified: bool,
    reply: str,
    used_rag: bool = False,
) -> dict[str, Any]:
    weights = {
        "coverage": 0.32,
        "confidence": 0.48,
        "freshness": 0.20,
    }

    coverage_score, coverage_signals = _coverage_score(evidence_results, tools_used)
    confidence_score, confidence_signals = _confidence_score(
        verified=verified,
        warnings=warnings,
        evidence_results=evidence_results,
        reply=reply,
        used_rag=used_rag,
    )
    freshness_score, freshness_signals = _freshness_score(evidence_results)

    overall = int(round(
        (weights["coverage"] * coverage_score)
        + (weights["confidence"] * confidence_score)
        + (weights["freshness"] * freshness_score)
    ))

    applied_caps: list[str] = []
    if confidence_score < 45:
        overall = min(overall, 59)
        applied_caps.append("confidence_below_45_cap_59")
    if freshness_score < 40 and confidence_score < 55:
        overall = min(overall, 62)
        applied_caps.append("stale_and_low_confidence_cap_62")
    if coverage_score < 35:
        overall = min(overall, 55)
        applied_caps.append("coverage_below_35_cap_55")

    overall = int(_clamp(float(overall), lo=0.0, hi=100.0))
    level = _quality_level(overall)

    return {
        "score": overall,
        "level": level,
        "summary": _summary(level, coverage_score, confidence_score, freshness_score),
        "dimensions": {
            "coverage": {"score": coverage_score, "signals": coverage_signals},
            "confidence": {"score": confidence_score, "signals": confidence_signals},
            "freshness": {"score": freshness_score, "signals": freshness_signals},
        },
        "calibration": {
            "version": "v2-business-priority",
            "weights": weights,
            "caps_applied": applied_caps,
        },
        "intent": intent,
    }
