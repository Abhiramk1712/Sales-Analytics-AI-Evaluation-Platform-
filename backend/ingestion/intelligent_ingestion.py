"""
backend/ingestion/intelligent_ingestion.py
=========================================
Inspects arbitrary CSV sources, infers business entities/columns,
normalizes into the platform schema, and loads to DB.
"""
from __future__ import annotations

import csv
import hashlib
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from backend.data_generator import (
    TABLE_ORDER,
    _collect_csv_ingestion_runs,
    _safe_company_dir_name,
    _write_dataset_to_csv,
    _write_ingestion_audit,
)
from backend.database import get_session_factory, Base, get_engine
from backend.ingestion.loaders.pdf_loader import PDFLoader
from backend.ingestion.loaders.excel_loader import ExcelLoader
from backend.ingestion.manifest_loader import build_manifest_canonical_dataset
from backend.models import Team, Rep, Quota, Account, Deal, Activity, Revenue
from backend.validation.revops_rules import RevOpsBusinessRuleValidator

ENTITY_FIELDS: dict[str, list[str]] = {
    "teams": ["id", "name", "region"],
    "reps": ["id", "team_id", "name", "email", "region", "hire_date"],
    "quotas": ["rep_id", "period", "amount"],
    "accounts": ["id", "name", "industry", "employee_count", "annual_revenue"],
    "deals": [
        "id",
        "account_id",
        "rep_id",
        "name",
        "product",
        "stage",
        "amount",
        "close_probability",
        "expected_close_date",
        "actual_close_date",
        "created_at",
    ],
    "activities": ["id", "deal_id", "rep_id", "type", "outcome", "notes", "activity_date"],
    "revenue": ["rep_id", "period", "amount"],
}

SYNONYMS: dict[str, dict[str, list[str]]] = {
    "teams": {
        "id": ["id", "team_id", "TeamId", "GroupId"],
        "name": ["name", "team", "team_name", "sales_team", "TeamName", "GroupName"],
        "region": ["region", "territory", "geo", "Region", "Geography", "Geo__c"],
    },
    "reps": {
        "id": ["id", "rep_id", "salesperson_id", "owner_id", "user_id",
               # Salesforce
               "OwnerId", "UserId", "Id",
               # HubSpot
               "hs_object_id", "hubspot_owner_id"],
        "team_id": ["team_id", "team", "sales_team_id", "TeamId", "GroupId"],
        "name": ["name", "rep", "rep_name", "salesperson", "salesperson_name", "owner_name",
                 # Salesforce
                 "Name", "Full_Name__c", "Owner_Name",
                 # HubSpot
                 "firstname", "lastname", "full_name"],
        "email": ["email", "rep_email", "salesperson_email", "owner_email", "user_email",
                  # Salesforce
                  "Email", "Owner_Email__c",
                  # HubSpot
                  "hs_email_domain", "owner_email"],
        "region": ["region", "territory", "geo", "Region", "Territory__c", "Geography__c"],
        "hire_date": ["hire_date", "start_date", "joined_on", "joined_date",
                      "StartDate", "Hire_Date__c", "EmploymentStartDate"],
    },
    "quotas": {
        "rep_id": ["rep_id", "salesperson_id", "owner_id", "user_id",
                   "rep_email", "owner_email", "salesperson_email",
                   # Salesforce
                   "OwnerId", "UserId", "Rep_Email__c"],
        "period": ["period", "quarter", "month", "fiscal_period",
                   # Salesforce
                   "QuotaPeriod", "FiscalQuarter", "FiscalYear", "Period__c",
                   # HubSpot / generic
                   "fiscal_period", "quota_period", "time_period"],
        "amount": ["amount", "quota", "target", "quota_amount", "target_amount",
                   # Salesforce
                   "QuotaAmount", "Quota__c", "Target__c", "AnnualRevenue",
                   # HubSpot
                   "quota_amount", "revenue_goal"],
    },
    "accounts": {
        "id": ["id", "account_id", "customer_id", "company_id",
               # Salesforce
               "Id", "AccountId", "Account_ID__c",
               # HubSpot
               "hs_object_id", "company_id"],
        "name": ["name", "account", "account_name", "company", "company_name", "customer_name",
                 # Salesforce
                 "Name", "Account_Name__c",
                 # HubSpot
                 "name", "company"],
        "industry": ["industry", "vertical", "sector",
                     "Industry", "Industry__c", "Vertical__c"],
        "employee_count": ["employee_count", "employees", "headcount",
                           "NumberOfEmployees", "Employees__c", "num_employees"],
        "annual_revenue": ["annual_revenue", "arr", "company_revenue", "account_revenue",
                           "AnnualRevenue", "Annual_Revenue__c", "Revenue__c"],
    },
    "deals": {
        "id": ["id", "deal_id", "opportunity_id", "opp_id",
               # Salesforce
               "Id", "OpportunityId",
               # HubSpot
               "hs_object_id", "dealId"],
        "account_id": ["account_id", "customer_id", "company_id", "account",
                       # Salesforce
                       "AccountId", "Account_ID__c",
                       # HubSpot
                       "associatedcompanyid", "company_id"],
        "rep_id": ["rep_id", "owner_id", "salesperson_id", "owner_email", "rep_email",
                   # Salesforce
                   "OwnerId", "Owner_Email__c",
                   # HubSpot
                   "hubspot_owner_id", "owner_email"],
        "name": ["name", "deal_name", "opportunity", "opp_name",
                 # Salesforce
                 "Name", "Opportunity_Name__c",
                 # HubSpot
                 "dealname"],
        "product": ["product", "sku", "plan", "package",
                    "Product__c", "Product_Name__c", "deal_type"],
        "stage": ["stage", "pipeline_stage", "opportunity_stage", "status",
                  # Salesforce
                  "StageName", "Stage__c",
                  # HubSpot
                  "dealstage", "pipeline_stage"],
        "amount": ["amount", "deal_value", "value", "acv", "contract_value",
                   # Salesforce
                   "Amount", "ACV__c", "TCV__c", "ARR__c",
                   # HubSpot
                   "amount", "hs_acv"],
        "close_probability": ["close_probability", "probability", "win_probability",
                              "Probability", "Win_Probability__c", "hs_deal_stage_probability"],
        "expected_close_date": ["expected_close_date", "close_date", "target_close_date",
                                # Salesforce
                                "CloseDate", "Expected_Close_Date__c",
                                # HubSpot
                                "closedate"],
        "actual_close_date": ["actual_close_date", "won_date", "closed_date",
                              "Closed_Date__c", "Won_Date__c"],
        "created_at": ["created_at", "created_date", "created_on", "opened_at",
                       # Salesforce
                       "CreatedDate",
                       # HubSpot
                       "createdate", "hs_createdate"],
    },
    "activities": {
        "id": ["id", "activity_id", "event_id", "task_id",
               # Salesforce
               "Id", "TaskId", "EventId"],
        "deal_id": ["deal_id", "opportunity_id", "opp_id",
                    "WhatId", "What_Id__c"],
        "rep_id": ["rep_id", "owner_id", "salesperson_id", "owner_email", "rep_email",
                   "OwnerId", "Owner_Email__c"],
        "type": ["type", "activity_type", "event_type", "touchpoint",
                 # Salesforce
                 "Type", "TaskSubtype", "Subject"],
        "outcome": ["outcome", "result", "status",
                    "Status", "Outcome__c", "Call_Result__c"],
        "notes": ["notes", "comment", "description", "details",
                  "Description", "Comments", "Body"],
        "activity_date": ["activity_date", "date", "event_date", "timestamp",
                          # Salesforce
                          "ActivityDate", "CreatedDate",
                          # HubSpot
                          "hs_timestamp", "engagement_date"],
    },
    "revenue": {
        "rep_id": ["rep_id", "owner_id", "salesperson_id", "owner_email", "rep_email",
                   "OwnerId", "Rep_Email__c"],
        "period": ["period", "month", "revenue_month", "date",
                   "Revenue_Period__c", "booking_month", "recognition_month"],
        "amount": ["amount", "revenue", "bookings", "booked_revenue", "recognized_revenue",
                   "Revenue__c", "Booking_Amount__c", "MRR__c", "ARR__c"],
    },
}

FILENAME_HINTS = {
    "teams": ["team", "territory", "group", "region"],
    "reps": ["rep", "salesperson", "owner", "user", "seller", "ae", "sdr"],
    "quotas": ["quota", "target", "goal", "plan"],
    "accounts": ["account", "company", "customer", "client", "org"],
    "deals": ["deal", "opp", "opportunity", "pipeline", "forecast"],
    "activities": ["activity", "touch", "event", "task", "call", "email"],
    "revenue": ["revenue", "bookings", "arr", "mrr", "booking", "recognized"],
}

STAGE_PROB = {
    "prospecting": 10,
    "qualification": 25,
    "proposal": 45,
    "negotiation": 75,
    "closed won": 100,
    "closed lost": 0,
}


@dataclass
class InferredFile:
    file_path: str
    source_type: str
    entity: str
    score: int
    mapping: dict[str, str]
    columns: list[str]
    row_count: int


def _norm(s: str) -> str:
    return "".join(ch for ch in s.lower().strip().replace("-", "_").replace(" ", "_") if ch.isalnum() or ch == "_")


def _load_preview_rows(path: Path, limit: int = 200) -> list[dict[str, Any]]:
    with open(path, "r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = []
        for i, row in enumerate(reader):
            if i >= limit:
                break
            rows.append({k: (v.strip() if isinstance(v, str) else v) for k, v in row.items()})
    return rows


def _load_pdf_preview_rows(path: Path, limit: int = 200) -> list[dict[str, Any]]:
    return PDFLoader().preview_rows(str(path), limit=limit)


def _load_pdf_rows(path: Path) -> list[dict[str, Any]]:
    return PDFLoader().extract_rows(str(path))


def _load_excel_preview_rows(path: Path, limit: int = 200) -> list[dict[str, Any]]:
    """Load up to `limit` rows from the first sheet of an Excel file."""
    try:
        loader = ExcelLoader()
        df, _ = loader.load(str(path), nrows=limit)
        # Convert NaN → empty string for consistency with CSV loader
        df = df.fillna("")
        return df.to_dict(orient="records")
    except Exception:
        return []


def _load_excel_all_rows(path: Path) -> dict[str, list[dict[str, Any]]]:
    """Load all sheets from an Excel file as {sheet_name: [rows]}."""
    try:
        loader = ExcelLoader()
        sheets = loader.load_all_sheets(str(path))
        result: dict[str, list[dict[str, Any]]] = {}
        for name, (df, _) in sheets.items():
            df = df.fillna("")
            result[name] = df.to_dict(orient="records")
        return result
    except Exception:
        return {}



    normalized = ",".join(sorted(_norm(c) for c in columns))
    return hashlib.md5(normalized.encode("utf-8")).hexdigest()


def _schema_fingerprint(columns: list[str]) -> str:
    normalized = ",".join(sorted(_norm(c) for c in columns))
    return hashlib.md5(normalized.encode("utf-8")).hexdigest()


def _source_manifest_entry(path: Path, source_type: str, columns: list[str], row_count: int) -> dict[str, Any]:
    source_id = str(uuid.uuid5(uuid.NAMESPACE_URL, str(path.resolve())))
    return {
        "source_id": source_id,
        "file_path": str(path),
        "file_name": path.name,
        "source_type": source_type,
        "row_count": row_count,
        "columns": columns,
        "schema_fingerprint": _schema_fingerprint(columns),
    }


def _guess_entity(file_name: str, columns: list[str]) -> tuple[str, int]:
    norm_cols = [_norm(c) for c in columns]
    best = ("unknown", 0)
    for entity, field_map in SYNONYMS.items():
        score = 0
        for _, options in field_map.items():
            if any(_norm(opt) in norm_cols for opt in options):
                score += 1
        hint_score = sum(1 for hint in FILENAME_HINTS[entity] if hint in _norm(file_name))
        score += 2 * hint_score
        if score > best[1]:
            best = (entity, score)
    return best


def _infer_field_mapping(entity: str, columns: list[str]) -> dict[str, str]:
    norm_to_raw = {_norm(c): c for c in columns}
    mapping: dict[str, str] = {}
    for field, options in SYNONYMS[entity].items():
        for option in options:
            if _norm(option) in norm_to_raw:
                mapping[field] = norm_to_raw[_norm(option)]
                break
    return mapping


def inspect_source_directory(source_dir: str) -> dict[str, Any]:
    base = Path(source_dir)
    if not base.exists() or not base.is_dir():
        raise FileNotFoundError(f"Source directory not found: {source_dir}")

    inferred: list[InferredFile] = []
    source_manifest: list[dict[str, Any]] = []
    warnings: list[str] = []

    for path in sorted(base.iterdir()):
        if not path.is_file():
            continue
        suffix = path.suffix.lower()
        if suffix == ".csv":
            source_type = "csv"
            rows = _load_preview_rows(path)
            if not rows:
                warnings.append(f"{path.name}: no structured rows found; file skipped")
                continue
            columns = list(rows[0].keys())
            source_manifest.append(_source_manifest_entry(path, source_type, columns, len(rows)))
            entity, score = _guess_entity(path.name, columns)
            if entity == "unknown" or score < 2:
                warnings.append(f"{path.name}: could not confidently map to a known entity")
                continue
            mapping = _infer_field_mapping(entity, columns)
            inferred.append(InferredFile(
                file_path=str(path), source_type=source_type, entity=entity,
                score=score, mapping=mapping, columns=columns, row_count=len(rows),
            ))
        elif suffix == ".pdf":
            source_type = "pdf"
            rows = _load_pdf_preview_rows(path)
            if not rows:
                warnings.append(f"{path.name}: no structured rows found; file skipped")
                continue
            columns = list(rows[0].keys())
            source_manifest.append(_source_manifest_entry(path, source_type, columns, len(rows)))
            entity, score = _guess_entity(path.name, columns)
            if entity == "unknown" or score < 2:
                warnings.append(f"{path.name}: could not confidently map to a known entity")
                continue
            mapping = _infer_field_mapping(entity, columns)
            inferred.append(InferredFile(
                file_path=str(path), source_type=source_type, entity=entity,
                score=score, mapping=mapping, columns=columns, row_count=len(rows),
            ))
        elif suffix in (".xlsx", ".xls"):
            # Each sheet is a separate ingestion candidate
            all_sheets = _load_excel_all_rows(path)
            if not all_sheets:
                warnings.append(f"{path.name}: no readable sheets found; file skipped")
                continue
            for sheet_name, rows in all_sheets.items():
                if not rows:
                    warnings.append(f"{path.name}#{sheet_name}: empty sheet skipped")
                    continue
                columns = list(rows[0].keys())
                # Virtual path for multi-sheet identification
                virtual_path = path.parent / f"{path.stem}__{sheet_name}{path.suffix}"
                hint_name = f"{path.stem}_{sheet_name}"
                source_manifest.append(_source_manifest_entry(path, "excel", columns, len(rows)))
                entity, score = _guess_entity(hint_name, columns)
                if entity == "unknown" or score < 2:
                    warnings.append(f"{path.name}#{sheet_name}: could not confidently map to a known entity")
                    continue
                mapping = _infer_field_mapping(entity, columns)
                inferred.append(InferredFile(
                    file_path=str(virtual_path), source_type="excel", entity=entity,
                    score=score, mapping=mapping, columns=columns, row_count=len(rows),
                ))
        else:
            continue

    return {
        "source_dir": str(base.resolve()),
        "files": [
            {
                "file_path": f.file_path,
                "source_type": f.source_type,
                "entity": f.entity,
                "score": f.score,
                "mapping": f.mapping,
                "columns": f.columns,
                "row_count": f.row_count,
            }
            for f in inferred
        ],
        "source_manifest": source_manifest,
        "warnings": warnings,
    }


# ── Canonical stage normalisation ─────────────────────────────────────────
CANONICAL_STAGE_MAP: dict[str, str] = {
    "prospecting": "Prospecting",
    "qualification": "Qualification",
    "proposal": "Proposal",
    "negotiation": "Negotiation",
    "closed won": "Closed Won",
    "won": "Closed Won",
    "closed_won": "Closed Won",
    "closedwon": "Closed Won",
    "closed lost": "Closed Lost",
    "lost": "Closed Lost",
    "closed_lost": "Closed Lost",
    "closedlost": "Closed Lost",
}


def _canonicalize_stage(v: Any) -> str:
    if not v:
        return "Qualification"
    norm = str(v).strip().lower()
    return CANONICAL_STAGE_MAP.get(norm, str(v).strip())


def _canonicalize_period(v: Any) -> str:
    """Normalise diverse period formats → YYYY-MM or YYYY-Qn."""
    if not v:
        return ""
    raw = str(v).strip()
    import re as _re
    if _re.match(r"^\d{4}-\d{2}$", raw):
        return raw
    if _re.match(r"^\d{4}-Q[1-4]$", raw):
        return raw
    if m := _re.match(r"^Q([1-4])[/\s]?(\d{4})$", raw, _re.IGNORECASE):
        return f"{m.group(2)}-Q{m.group(1)}"
    if m := _re.match(r"^(\d{4})[/\s-]Q([1-4])$", raw, _re.IGNORECASE):
        return f"{m.group(1)}-Q{m.group(2)}"
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%Y/%m/%d"):
        try:
            from datetime import datetime as _dt
            return _dt.strptime(raw, fmt).strftime("%Y-%m")
        except ValueError:
            pass
    return raw


# ── Quality gate severity policy ───────────────────────────────────────────
QUALITY_SEVERITY = {
    "empty_reps": "critical",
    "empty_deals": "high",
    "empty_accounts": "high",
    "empty_teams": "medium",
    "empty_quotas": "medium",
    "empty_revenue": "medium",
    "missing_team_refs": "high",
    "missing_account_refs": "high",
    "negative_amounts": "medium",
    "unresolved_period_formats": "low",
    "relationship_unresolved_required": "high",
    "relationship_unresolved_optional": "low",
}

SEVERITY_CONFIDENCE_PENALTY = {
    "critical": -1.0,
    "high": -0.30,
    "medium": -0.10,
    "low": -0.03,
}


def evaluate_quality_gates(
    dataset: dict[str, list[dict[str, str]]],
    warnings: list[str],
    relationship_resolution: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Evaluate severity-based quality gates on a canonical dataset.

    Returns a quality summary with overall confidence score (0.0-1.0), a list of
    issues by severity, and a 'blocked' flag for critical failures (soft-proceed policy
    still runs but surfaces the block in outputs).
    """
    issues: list[dict[str, Any]] = []
    confidence = 1.0

    def _add(key: str, msg: str) -> None:
        sev = QUALITY_SEVERITY.get(key, "low")
        issues.append({"key": key, "severity": sev, "message": msg})
        nonlocal confidence
        confidence = max(0.0, confidence + SEVERITY_CONFIDENCE_PENALTY[sev])

    # Critical: reps
    if not dataset.get("reps"):
        _add("empty_reps", "No rep records produced; downstream ML and payout computation cannot run.")

    # High: deals / accounts
    if not dataset.get("deals"):
        _add("empty_deals", "No deal records; deal scoring and pipeline metrics unavailable.")
    if not dataset.get("accounts"):
        _add("empty_accounts", "No account records; deal account linkage will be incomplete.")

    # Medium: structural
    if not dataset.get("teams"):
        _add("empty_teams", "No team records; rep-to-team assignments will default to placeholder.")
    if not dataset.get("quotas"):
        _add("empty_quotas", "No quota records; attainment and payout calculations will be estimated.")
    if not dataset.get("revenue"):
        _add("empty_revenue", "No revenue records; forecasting and payout will be unavailable.")

    # High: foreign-key consistency
    rep_ids = {r["id"] for r in dataset.get("reps", [])}
    team_ids = {t["id"] for t in dataset.get("teams", [])}
    orphan_reps = [r["id"] for r in dataset.get("reps", []) if r.get("team_id") and r["team_id"] not in team_ids]
    if orphan_reps:
        _add("missing_team_refs", f"{len(orphan_reps)} reps reference unknown teams.")
    account_ids = {a["id"] for a in dataset.get("accounts", [])}
    orphan_deals = [d for d in dataset.get("deals", []) if d.get("account_id") and d["account_id"] not in account_ids]
    if orphan_deals:
        _add("missing_account_refs", f"{len(orphan_deals)} deals reference unknown accounts.")

    # Medium: negative amounts
    neg = sum(1 for r in dataset.get("revenue", []) if _to_float(r.get("amount", 0)) is not None and (_to_float(r.get("amount", 0)) or 0) < 0)
    if neg:
        _add("negative_amounts", f"{neg} revenue rows have negative amounts (may indicate clawbacks).")

    # Manifest relationship resolution penalties (optional, additive).
    relationship_penalty_applied = False
    required_unresolved_total = 0
    optional_unresolved_total = 0
    if relationship_resolution:
        for rel_id, summary in relationship_resolution.items():
            unresolved = int(summary.get("unresolved", 0) or 0)
            if unresolved <= 0:
                continue
            relationship_penalty_applied = True
            if bool(summary.get("required", False)):
                required_unresolved_total += unresolved
                _add(
                    "relationship_unresolved_required",
                    f"Relationship '{rel_id}' has {unresolved} unresolved required row(s).",
                )
            else:
                optional_unresolved_total += unresolved
                _add(
                    "relationship_unresolved_optional",
                    f"Relationship '{rel_id}' has {unresolved} unresolved optional row(s).",
                )

    blocked = any(i["severity"] == "critical" for i in issues)
    overall_status = "critical" if blocked else ("high" if any(i["severity"] == "high" for i in issues) else ("medium" if issues else "ok"))

    return {
        "confidence": round(confidence, 3),
        "overall_status": overall_status,
        "blocked": blocked,
        "issues": issues,
        "data_warnings": warnings,
        "relationship_quality": {
            "applied": relationship_penalty_applied,
            "required_unresolved": required_unresolved_total,
            "optional_unresolved": optional_unresolved_total,
        },
    }


def _to_float(v: Any) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return None


def _to_int(v: Any) -> int | None:
    f = _to_float(v)
    return int(f) if f is not None else None


def _to_date(v: Any) -> str:
    if not v:
        return ""
    raw = str(v).strip()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(raw, fmt).date().isoformat()
        except ValueError:
            continue
    return raw[:10]


def _to_datetime(v: Any) -> str:
    if not v:
        return datetime.now(UTC).isoformat()
    raw = str(v).strip()
    if "T" in raw:
        return raw
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(raw, fmt).isoformat()
        except ValueError:
            continue
    return datetime.now(UTC).isoformat()


def _canonical_uuid_from_source(source_ref: str | None) -> str:
    if source_ref:
        ref = str(source_ref).strip()
        if ref:
            try:
                return str(uuid.UUID(ref))
            except ValueError:
                pass
    return str(uuid.uuid4())


def _read_all_rows(path: str) -> list[dict[str, Any]]:
    with open(path, "r", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _read_rows_for_inferred_item(item: dict[str, Any]) -> list[dict[str, Any]]:
    source_type = str(item.get("source_type", "csv")).lower()
    path = Path(item["file_path"])
    if source_type == "pdf":
        return _load_pdf_rows(path)
    if source_type == "excel":
        # Virtual path format: stem__SheetName.xlsx — extract real path + sheet
        real_suffix = path.suffix
        name_part = path.stem  # e.g. "workbook__Sheet1"
        if "__" in name_part:
            real_stem, sheet_name = name_part.rsplit("__", 1)
            real_path = path.parent / f"{real_stem}{real_suffix}"
            sheets = _load_excel_all_rows(real_path)
            return sheets.get(sheet_name, [])
        # Single-sheet fallback
        return _load_excel_preview_rows(path, limit=999999)
    return _read_all_rows(str(path))


def build_canonical_dataset(inspection: dict[str, Any]) -> tuple[dict[str, list[dict[str, str]]], list[str]]:
    dataset: dict[str, list[dict[str, str]]] = {name: [] for name in TABLE_ORDER}
    warnings: list[str] = list(inspection.get("warnings", []))

    reps_by_email: dict[str, str] = {}
    reps_by_name: dict[str, str] = {}
    reps_by_source_ref: dict[str, str] = {}
    accounts_by_name: dict[str, str] = {}
    accounts_by_source_ref: dict[str, str] = {}
    teams_by_name: dict[str, str] = {}
    deals_by_source_ref: dict[str, str] = {}

    inferred = inspection.get("files", [])

    # First pass: teams, reps, accounts for downstream foreign keys.
    for item in inferred:
        entity = item["entity"]
        mapping = item["mapping"]
        rows = _read_rows_for_inferred_item(item)

        if entity == "teams":
            for row in rows:
                team_id = str(uuid.uuid4())
                name = row.get(mapping.get("name", ""), "Unknown Team")
                region = row.get(mapping.get("region", ""), "Unknown")
                dataset["teams"].append({"id": team_id, "name": str(name), "region": str(region)})
                teams_by_name[_norm(str(name))] = team_id

        if entity == "reps":
            for row in rows:
                source_id_ref = str(row.get(mapping.get("id", ""), "")).strip()
                rep_id = _canonical_uuid_from_source(source_id_ref)
                rep_name = str(row.get(mapping.get("name", ""), "Unknown Rep"))
                rep_email = str(row.get(mapping.get("email", ""), "")).lower()
                region = str(row.get(mapping.get("region", ""), "Unknown"))
                team_hint = str(row.get(mapping.get("team_id", ""), ""))
                team_id = teams_by_name.get(_norm(team_hint))
                if not team_id:
                    # Backfill a region-based team when source has no explicit team table.
                    team_key = _norm(f"{region} Sales Team")
                    if team_key not in teams_by_name:
                        team_id = str(uuid.uuid4())
                        teams_by_name[team_key] = team_id
                        dataset["teams"].append({"id": team_id, "name": f"{region} Sales Team", "region": region})
                    else:
                        team_id = teams_by_name[team_key]

                dataset["reps"].append(
                    {
                        "id": rep_id,
                        "team_id": team_id,
                        "name": rep_name,
                        "email": rep_email or f"{_norm(rep_name)}@example.com",
                        "region": region,
                        "hire_date": _to_date(row.get(mapping.get("hire_date", ""))) or date.today().isoformat(),
                    }
                )
                if rep_email:
                    reps_by_email[rep_email] = rep_id
                reps_by_name[_norm(rep_name)] = rep_id
                if source_id_ref:
                    reps_by_source_ref[source_id_ref.lower()] = rep_id

        if entity == "accounts":
            for row in rows:
                source_id_ref = str(row.get(mapping.get("id", ""), "")).strip()
                account_id = _canonical_uuid_from_source(source_id_ref)
                name = str(row.get(mapping.get("name", ""), "Unknown Account"))
                dataset["accounts"].append(
                    {
                        "id": account_id,
                        "name": name,
                        "industry": str(row.get(mapping.get("industry", ""), "Unknown")),
                        "employee_count": str(_to_int(row.get(mapping.get("employee_count", ""))) or 0),
                        "annual_revenue": str(_to_float(row.get(mapping.get("annual_revenue", ""))) or 0.0),
                    }
                )
                accounts_by_name[_norm(name)] = account_id
                if source_id_ref:
                    accounts_by_source_ref[source_id_ref.lower()] = account_id

    # Ensure minimum structures exist.
    if not dataset["teams"]:
        team_id = str(uuid.uuid4())
        dataset["teams"].append({"id": team_id, "name": "General Sales Team", "region": "Unknown"})
        teams_by_name[_norm("General Sales Team")] = team_id

    # Create placeholder accounts when deal source references unseen accounts.
    def ensure_account(name: str) -> str:
        key = _norm(name)
        if key in accounts_by_name:
            return accounts_by_name[key]
        account_id = str(uuid.uuid4())
        accounts_by_name[key] = account_id
        dataset["accounts"].append(
            {
                "id": account_id,
                "name": name,
                "industry": "Unknown",
                "employee_count": "0",
                "annual_revenue": "0.0",
            }
        )
        return account_id

    def resolve_rep_id(row: dict[str, Any], mapping: dict[str, str]) -> str | None:
        rep_ref = str(row.get(mapping.get("rep_id", ""), "")).strip().lower()
        if rep_ref in reps_by_email:
            return reps_by_email[rep_ref]
        if rep_ref in reps_by_source_ref:
            return reps_by_source_ref[rep_ref]
        if _norm(rep_ref) in reps_by_name:
            return reps_by_name[_norm(rep_ref)]
        return None

    # Second pass: transactional tables in dependency order.
    second_pass_priority = {"deals": 0, "quotas": 1, "revenue": 2, "activities": 3}
    second_pass_items = sorted(
        [item for item in inferred if item["entity"] in second_pass_priority],
        key=lambda item: second_pass_priority[item["entity"]],
    )

    for item in second_pass_items:
        entity = item["entity"]
        mapping = item["mapping"]
        rows = _read_rows_for_inferred_item(item)

        if entity == "deals":
            for row in rows:
                rep_id = resolve_rep_id(row, mapping)
                if not rep_id and dataset["reps"]:
                    rep_id = dataset["reps"][0]["id"]
                source_account_ref = str(row.get(mapping.get("account_id", ""), "")).strip()
                if source_account_ref.lower() in accounts_by_source_ref:
                    account_id = accounts_by_source_ref[source_account_ref.lower()]
                else:
                    account_id = ensure_account(source_account_ref or "Unknown Account")
                stage = _canonicalize_stage(row.get(mapping.get("stage", ""), "")) or "Qualification"
                close_probability = _to_int(row.get(mapping.get("close_probability", "")))
                if close_probability is None:
                    close_probability = STAGE_PROB.get(stage.lower(), 30)
                created_at = _to_datetime(row.get(mapping.get("created_at", "")))
                expected = _to_date(row.get(mapping.get("expected_close_date", "")))
                if not expected:
                    expected = (datetime.fromisoformat(created_at).date() + timedelta(days=60)).isoformat()
                actual = _to_date(row.get(mapping.get("actual_close_date", "")))

                source_deal_ref = str(row.get(mapping.get("id", ""), "")).strip()
                deal_id = _canonical_uuid_from_source(source_deal_ref)
                dataset["deals"].append(
                    {
                        "id": deal_id,
                        "account_id": account_id,
                        "rep_id": rep_id or "",
                        "name": str(row.get(mapping.get("name", ""), "Imported Deal")),
                        "product": str(row.get(mapping.get("product", ""), "Unknown Product")),
                        "stage": stage,
                        "amount": str(_to_float(row.get(mapping.get("amount", ""))) or 0.0),
                        "close_probability": str(close_probability),
                        "expected_close_date": expected,
                        "actual_close_date": actual,
                        "created_at": created_at,
                    }
                )
                if source_deal_ref:
                    deals_by_source_ref[source_deal_ref.lower()] = deal_id

        if entity == "quotas":
            for row in rows:
                rep_id = resolve_rep_id(row, mapping)
                if not rep_id:
                    warnings.append("quota row skipped: could not resolve rep_id")
                    continue
                raw_period = str(row.get(mapping.get("period", ""), "")).strip()
                period = _canonicalize_period(raw_period) or f"{date.today().year}-Q{((date.today().month - 1)//3)+1}"
                dataset["quotas"].append(
                    {
                        "rep_id": rep_id,
                        "period": period,
                        "amount": str(_to_float(row.get(mapping.get("amount", ""))) or 0.0),
                    }
                )

        if entity == "revenue":
            for row in rows:
                rep_id = resolve_rep_id(row, mapping)
                if not rep_id:
                    warnings.append("revenue row skipped: could not resolve rep_id")
                    continue
                raw_period = str(row.get(mapping.get("period", ""), "")).strip()
                period = _canonicalize_period(raw_period) or date.today().strftime("%Y-%m")
                dataset["revenue"].append(
                    {
                        "rep_id": rep_id,
                        "period": period,
                        "amount": str(_to_float(row.get(mapping.get("amount", ""))) or 0.0),
                    }
                )

        if entity == "activities":
            for row in rows:
                rep_id = resolve_rep_id(row, mapping)
                deal_ref = str(row.get(mapping.get("deal_id", ""), "")).strip()
                deal_id = deals_by_source_ref.get(deal_ref.lower(), deal_ref)
                if not deal_id and dataset["deals"]:
                    deal_id = dataset["deals"][0]["id"]
                if not deal_id:
                    warnings.append("activity row skipped: could not resolve deal_id")
                    continue
                dataset["activities"].append(
                    {
                        "id": str(uuid.uuid4()),
                        "deal_id": deal_id,
                        "rep_id": rep_id or "",
                        "type": str(row.get(mapping.get("type", ""), "call")),
                        "outcome": str(row.get(mapping.get("outcome", ""), "neutral")),
                        "notes": str(row.get(mapping.get("notes", ""), "Imported activity")),
                        "activity_date": _to_datetime(row.get(mapping.get("activity_date", ""))),
                    }
                )

    # Backfill quotas/revenue if missing so payouts and analytics remain functional.
    if not dataset["quotas"]:
        current_q = f"{date.today().year}-Q{((date.today().month - 1)//3)+1}"
        for rep in dataset["reps"]:
            dataset["quotas"].append({"rep_id": rep["id"], "period": current_q, "amount": "500000"})
        warnings.append("No quota source detected; default quarterly quotas were generated")

    if not dataset["revenue"]:
        month = date.today().strftime("%Y-%m")
        for rep in dataset["reps"]:
            dataset["revenue"].append({"rep_id": rep["id"], "period": month, "amount": "0"})
        warnings.append("No revenue source detected; zero monthly revenue rows were generated")

    return dataset, warnings


def _parse_optional_date(raw: str | None) -> date | None:
    if not raw:
        return None
    return date.fromisoformat(raw)


def _parse_datetime(raw: str) -> datetime:
    return datetime.fromisoformat(raw)


async def _load_dataset_to_db(dataset: dict[str, list[dict[str, str]]], reset_database: bool = True) -> dict[str, int]:
    if reset_database:
        _engine = get_engine()
        async with _engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
            await conn.run_sync(Base.metadata.create_all)

    _session_factory = get_session_factory()
    async with _session_factory() as db:
        return await _insert_dataset_rows(db, dataset)


async def _insert_dataset_rows(db: AsyncSession, dataset: dict[str, list[dict[str, str]]]) -> dict[str, int]:
    counts: dict[str, int] = {}

    for row in dataset["teams"]:
        db.add(Team(id=uuid.UUID(row["id"]), name=row["name"], region=row.get("region")))
    counts["teams"] = len(dataset["teams"])
    await db.flush()

    for row in dataset["reps"]:
        db.add(
            Rep(
                id=uuid.UUID(row["id"]),
                team_id=uuid.UUID(row["team_id"]),
                name=row["name"],
                email=row["email"],
                region=row.get("region"),
                hire_date=_parse_optional_date(row.get("hire_date")),
            )
        )
    counts["reps"] = len(dataset["reps"])
    await db.flush()

    for row in dataset["quotas"]:
        db.add(Quota(rep_id=uuid.UUID(row["rep_id"]), period=row["period"], amount=float(row["amount"])))
    counts["quotas"] = len(dataset["quotas"])
    await db.flush()

    for row in dataset["accounts"]:
        db.add(
            Account(
                id=uuid.UUID(row["id"]),
                name=row["name"],
                industry=row.get("industry"),
                employee_count=int(row["employee_count"]) if row.get("employee_count") else None,
                annual_revenue=float(row["annual_revenue"]) if row.get("annual_revenue") else None,
            )
        )
    counts["accounts"] = len(dataset["accounts"])
    await db.flush()

    for row in dataset["deals"]:
        db.add(
            Deal(
                id=uuid.UUID(row["id"]),
                account_id=uuid.UUID(row["account_id"]),
                rep_id=uuid.UUID(row["rep_id"]) if row.get("rep_id") else None,
                name=row["name"],
                product=row.get("product"),
                stage=row["stage"],
                amount=float(row["amount"]),
                close_probability=int(row["close_probability"]) if row.get("close_probability") else None,
                expected_close_date=_parse_optional_date(row.get("expected_close_date")),
                actual_close_date=_parse_optional_date(row.get("actual_close_date")),
                created_at=_parse_datetime(row["created_at"]),
            )
        )
    counts["deals"] = len(dataset["deals"])
    await db.flush()

    for row in dataset["activities"]:
        db.add(
            Activity(
                id=uuid.UUID(row["id"]),
                deal_id=uuid.UUID(row["deal_id"]),
                rep_id=uuid.UUID(row["rep_id"]) if row.get("rep_id") else None,
                type=row.get("type"),
                outcome=row.get("outcome"),
                notes=row.get("notes"),
                activity_date=_parse_datetime(row["activity_date"]),
            )
        )
    counts["activities"] = len(dataset["activities"])
    await db.flush()

    for row in dataset["revenue"]:
        db.add(Revenue(rep_id=uuid.UUID(row["rep_id"]), period=row["period"], amount=float(row["amount"])))
    counts["revenue"] = len(dataset["revenue"])

    await db.commit()
    return counts


async def intelligent_ingest(
    source_dir: str,
    company_name: str,
    reset_database: bool = True,
    load_mode: str = "full_reload",
    use_manifest: bool = True,
    manifest_name: str = "sales_schema",
    manifest_version: str = "v1",
) -> dict[str, Any]:
    """End-to-end ingestion: inspect → canonicalize → quality gate → load → audit.

    load_mode options:
      'full_reload' – drop/recreate tables then insert (requires reset_database=True)
      'upsert'      – merge on primary key; inserts or updates existing rows
      'append'      – insert only rows whose (rep_id, period) / id are not already present
    Destructive modes (full_reload with reset_database=True) require explicit opt-in via
    reset_database=True and are surfaced in the response so the agent can confirm.
    """
    inspection = inspect_source_directory(source_dir)

    manifest_details: dict[str, Any] | None = None
    if use_manifest:
        try:
            dataset, transform_warnings, manifest_details = build_manifest_canonical_dataset(
                inspection=inspection,
                manifest_name=manifest_name,
                manifest_version=manifest_version,
            )
        except Exception as exc:
            dataset, transform_warnings = build_canonical_dataset(inspection)
            transform_warnings.append(
                f"Manifest path fallback: {exc}. Used legacy canonical ingestion path."
            )
    else:
        dataset, transform_warnings = build_canonical_dataset(inspection)

    quality = evaluate_quality_gates(
        dataset,
        transform_warnings,
        relationship_resolution=(manifest_details or {}).get("relationship_resolution", {}),
    )
    all_warnings = list(transform_warnings)
    for issue in quality["issues"]:
        all_warnings.append(f"[{issue['severity'].upper()}] {issue['message']}")

    company_slug = _safe_company_dir_name(company_name)
    source_path = Path(source_dir).resolve()
    target_path = (Path("companies") / company_slug).resolve()
    output_company_slug = company_slug
    if source_path == target_path:
        output_company_slug = f"{company_slug}-normalized"

    company_dir = _write_dataset_to_csv(dataset, company_name=output_company_slug, base_dir="companies")

    # RevOps business-rule validation (hard-fail blocks DB load)
    revops_validator = RevOpsBusinessRuleValidator()
    revops_result = revops_validator.validate(company_dir)
    revops_summary = revops_result.summary()
    if not revops_result.passed:
        # Surface hard-fail messages as warnings so the caller can inspect them;
        # we do not raise here to preserve audit trail — instead flag in response.
        for v in revops_result.violations:
            all_warnings.append(f"[REVOPS_HARD_FAIL] {v.rule}: {v.message}")
    for w in revops_result.warnings:
        all_warnings.append(f"[REVOPS_WARN] {w.rule}: {w.message}")

    effective_reset = reset_database and load_mode == "full_reload"
    db_counts = await _load_dataset_to_db(dataset, reset_database=effective_reset)

    ingestion_runs = _collect_csv_ingestion_runs(company_dir)
    audit_path = _write_ingestion_audit(
        company_dir=company_dir,
        company_name=company_name,
        db_counts=db_counts,
        runs=ingestion_runs,
    )

    return {
        "company_name": company_name,
        "company_dir": str(company_dir),
        "inspection": inspection,
        "source_manifest": inspection.get("source_manifest", []),
        "manifest_details": manifest_details,
        "quality_gate": quality,
        "revops_validation": revops_summary,
        "load_mode": load_mode,
        "use_manifest": use_manifest,
        "warnings": all_warnings,
        "db_rows_loaded": db_counts,
        "audit_file": str(audit_path),
        "inferred_entities": sorted({f["entity"] for f in inspection.get("files", [])}),
    }
