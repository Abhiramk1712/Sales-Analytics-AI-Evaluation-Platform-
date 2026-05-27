"""
backend/ingestion/manifest_loader.py
===================================
Manifest-driven mapping and canonical dataset builder.

This module maps source rows to manifest table specs, applies transform registry,
and builds the legacy canonical dataset so downstream services remain stable.
"""

from __future__ import annotations

import csv
import uuid
from datetime import date, datetime, UTC
from pathlib import Path
from typing import Any

from backend.data_generator import TABLE_ORDER
from backend.ingestion.loaders.pdf_loader import PDFLoader
from backend.ingestion.manifest_schema import ManifestSchema, TableSpec
from backend.ingestion.source_registry import get_manifest_registry
from backend.transformations.registry import apply_transform


ENTITY_TO_MANIFEST_TABLE = {
    "teams": ["teams"],
    "reps": ["users", "reps"],
    "accounts": ["accounts"],
    "deals": ["opportunities", "deals"],
    "quotas": ["quotas"],
    "activities": ["activities"],
    "revenue": ["revenue", "monthly_finance"],
}


def _norm(value: str) -> str:
    return "".join(ch for ch in value.lower().strip().replace("-", "_").replace(" ", "_") if ch.isalnum() or ch == "_")


def _load_rows(file_path: str, source_type: str) -> list[dict[str, Any]]:
    path = Path(file_path)
    if source_type == "pdf":
        return PDFLoader().extract_rows(str(path))
    with open(path, "r", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _first_present(row: dict[str, Any], candidates: list[str]) -> Any:
    norm_map = {_norm(k): k for k in row.keys()}
    for c in candidates:
        key = norm_map.get(_norm(c))
        if key is not None:
            return row.get(key)
    return None


def _table_for_entity(manifest: ManifestSchema, entity: str) -> str | None:
    for candidate in ENTITY_TO_MANIFEST_TABLE.get(entity, []):
        if manifest.get_table(candidate):
            return candidate
    return None


def _map_row_to_table_spec(row: dict[str, Any], table_spec: TableSpec) -> tuple[dict[str, Any], list[str]]:
    mapped: dict[str, Any] = {}
    missing_required: list[str] = []

    for col in table_spec.columns:
        candidates = [col.source_name, *col.synonyms]
        value = _first_present(row, candidates)
        if value is None or value == "":
            if not col.nullable:
                missing_required.append(col.target_name)
            continue
        mapped[col.target_name] = apply_transform(col.transform, value)

    return mapped, missing_required


def _normalize_match_value(value: Any, mode: str = "exact") -> str:
    if value is None:
        return ""
    raw = str(value).strip()
    if mode in {"case_insensitive", "email"}:
        return raw.lower()
    return raw


def _compose_key(values: list[Any], mode: str = "exact") -> tuple[str, ...]:
    return tuple(_normalize_match_value(v, mode) for v in values)


def _resolve_relationships_and_collect_provenance(
    manifest: ManifestSchema,
    mapped_rows_by_table: dict[str, list[dict[str, Any]]],
) -> tuple[dict[str, Any], list[str]]:
    """Validate/resolve relationships deterministically and collect row-level provenance."""
    warnings: list[str] = []
    relationship_summary: dict[str, Any] = {}
    row_provenance: dict[str, list[dict[str, Any]]] = {k: [] for k in mapped_rows_by_table.keys()}

    # Build initial provenance records for each mapped row.
    for table_name, rows in mapped_rows_by_table.items():
        for idx, _ in enumerate(rows):
            row_provenance[table_name].append(
                {
                    "source_row_id": idx,
                    "mapping_method": "manifest_column_mapping",
                    "confidence": 1.0,
                    "fallback_reason": None,
                    "relationship_resolution": [],
                }
            )

    for rel_id, rel in manifest.relationships.items():
        resolved = 0
        unresolved = 0
        optional_unresolved = 0

        if rel.direct_fk:
            fk = rel.direct_fk
            local_rows = mapped_rows_by_table.get(fk.local_table, [])
            remote_rows = mapped_rows_by_table.get(fk.remote_table, [])
            remote_values = {str(r.get(fk.remote_column)).strip() for r in remote_rows if r.get(fk.remote_column) is not None}

            for idx, row in enumerate(local_rows):
                local_val = row.get(fk.local_column)
                if local_val in (None, ""):
                    if fk.nullable:
                        optional_unresolved += 1
                        row_provenance[fk.local_table][idx]["relationship_resolution"].append(
                            {"relationship_id": rel_id, "type": "direct_fk", "status": "nullable-missing"}
                        )
                    else:
                        unresolved += 1
                        row_provenance[fk.local_table][idx]["relationship_resolution"].append(
                            {"relationship_id": rel_id, "type": "direct_fk", "status": "missing-required-value"}
                        )
                    continue

                if str(local_val).strip() in remote_values:
                    resolved += 1
                    row_provenance[fk.local_table][idx]["relationship_resolution"].append(
                        {"relationship_id": rel_id, "type": "direct_fk", "status": "resolved"}
                    )
                else:
                    unresolved += 1
                    row_provenance[fk.local_table][idx]["relationship_resolution"].append(
                        {
                            "relationship_id": rel_id,
                            "type": "direct_fk",
                            "status": "unresolved",
                            "fallback_reason": "referenced-value-not-found",
                        }
                    )

        elif rel.business_key:
            bk = rel.business_key
            local_rows = mapped_rows_by_table.get(bk.local_table, [])
            remote_rows = mapped_rows_by_table.get(bk.remote_table, [])
            mode = bk.match_type or "exact"
            remote_keys = {
                _compose_key([r.get(c) for c in bk.remote_columns], mode)
                for r in remote_rows
            }

            for idx, row in enumerate(local_rows):
                local_key = _compose_key([row.get(c) for c in bk.local_columns], mode)
                if all(v == "" for v in local_key):
                    unresolved += 1
                    row_provenance[bk.local_table][idx]["relationship_resolution"].append(
                        {"relationship_id": rel_id, "type": "business_key", "status": "missing-key"}
                    )
                    continue
                if local_key in remote_keys:
                    resolved += 1
                    row_provenance[bk.local_table][idx]["relationship_resolution"].append(
                        {"relationship_id": rel_id, "type": "business_key", "status": "resolved"}
                    )
                else:
                    unresolved += 1
                    row_provenance[bk.local_table][idx]["relationship_resolution"].append(
                        {
                            "relationship_id": rel_id,
                            "type": "business_key",
                            "status": "unresolved",
                            "fallback_reason": "business-key-not-found",
                        }
                    )

        elif rel.code_mapping:
            cm = rel.code_mapping
            local_rows = mapped_rows_by_table.get(cm.local_table, [])
            remote_rows = mapped_rows_by_table.get(cm.remote_table, [])
            remote_codes = {str(r.get(cm.remote_column)).strip().lower() for r in remote_rows if r.get(cm.remote_column) is not None}

            for idx, row in enumerate(local_rows):
                code = row.get(cm.local_column)
                if code is None or str(code).strip() == "":
                    unresolved += 1
                    row_provenance[cm.local_table][idx]["relationship_resolution"].append(
                        {"relationship_id": rel_id, "type": "code_mapping", "status": "missing-code"}
                    )
                    continue
                if str(code).strip().lower() in remote_codes:
                    resolved += 1
                    row_provenance[cm.local_table][idx]["relationship_resolution"].append(
                        {"relationship_id": rel_id, "type": "code_mapping", "status": "resolved"}
                    )
                else:
                    unresolved += 1
                    row_provenance[cm.local_table][idx]["relationship_resolution"].append(
                        {
                            "relationship_id": rel_id,
                            "type": "code_mapping",
                            "status": "unresolved",
                            "fallback_reason": "code-not-found",
                        }
                    )

        relationship_summary[rel_id] = {
            "type": rel.relationship_type.value,
            "resolved": resolved,
            "unresolved": unresolved,
            "optional_unresolved": optional_unresolved,
            "required": rel.is_required,
        }

        if unresolved > 0:
            severity = "required" if rel.is_required else "optional"
            warnings.append(
                f"relationship {rel_id}: {unresolved} unresolved row(s) ({severity})"
            )

    return {
        "relationship_summary": relationship_summary,
        "row_provenance": row_provenance,
    }, warnings


def _uuid_or_new(value: Any) -> str:
    if value is None:
        return str(uuid.uuid4())
    raw = str(value).strip()
    if not raw:
        return str(uuid.uuid4())
    try:
        return str(uuid.UUID(raw))
    except ValueError:
        return str(uuid.uuid5(uuid.NAMESPACE_DNS, raw.lower()))


def _to_float(value: Any, default: float = 0.0) -> float:
    if value is None or value == "":
        return default
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return default


def build_manifest_canonical_dataset(
    inspection: dict[str, Any],
    manifest_name: str = "sales_schema",
    manifest_version: str = "v1",
) -> tuple[dict[str, list[dict[str, str]]], list[str], dict[str, Any]]:
    """
    Build canonical dataset using manifest mappings and transform registry.

    Returns:
      dataset: Legacy canonical dataset (teams/reps/quotas/accounts/deals/activities/revenue)
      warnings: Mapping warnings and fallbacks
      metadata: Coverage and mapping diagnostics
    """
    registry = get_manifest_registry()
    manifest = registry.load_manifest(manifest_name, manifest_version)

    warnings: list[str] = []
    mapped_rows_by_table: dict[str, list[dict[str, Any]]] = {}
    mapping_stats: dict[str, dict[str, int]] = {}

    for inferred in inspection.get("files", []):
        entity = inferred.get("entity", "")
        table_name = _table_for_entity(manifest, entity)
        if not table_name:
            warnings.append(f"manifest mapping skipped: entity '{entity}' has no manifest table")
            continue

        table_spec = manifest.get_table(table_name)
        if not table_spec:
            warnings.append(f"manifest mapping skipped: table '{table_name}' missing from manifest")
            continue

        rows = _load_rows(inferred["file_path"], inferred.get("source_type", "csv"))
        accepted = 0
        rejected = 0

        for row in rows:
            mapped, missing_required = _map_row_to_table_spec(row, table_spec)
            if missing_required:
                rejected += 1
                warnings.append(
                    f"{Path(inferred['file_path']).name}: row dropped for table '{table_name}' (missing required: {', '.join(missing_required)})"
                )
                continue
            mapped_rows_by_table.setdefault(table_name, []).append(mapped)
            accepted += 1

        mapping_stats[table_name] = {
            "rows_seen": len(rows),
            "rows_mapped": accepted,
            "rows_dropped": rejected,
        }

    relationship_details, relationship_warnings = _resolve_relationships_and_collect_provenance(
        manifest=manifest,
        mapped_rows_by_table=mapped_rows_by_table,
    )
    warnings.extend(relationship_warnings)

    # Build legacy canonical dataset to preserve existing APIs.
    dataset: dict[str, list[dict[str, str]]] = {name: [] for name in TABLE_ORDER}

    teams_by_id: dict[str, str] = {}
    reps_by_id: dict[str, str] = {}
    reps_by_email: dict[str, str] = {}
    accounts_by_id: dict[str, str] = {}

    # teams
    for row in mapped_rows_by_table.get("teams", []):
        team_id = _uuid_or_new(row.get("id"))
        teams_by_id[team_id] = str(row.get("name") or "General Sales Team")
        dataset["teams"].append(
            {
                "id": team_id,
                "name": str(row.get("name") or "General Sales Team"),
                "region": str(row.get("region") or "Unknown"),
            }
        )

    if not dataset["teams"]:
        team_id = str(uuid.uuid4())
        dataset["teams"].append({"id": team_id, "name": "General Sales Team", "region": "Unknown"})
        teams_by_id[team_id] = "General Sales Team"
        warnings.append("manifest ingestion: generated fallback team")

    # reps/users
    user_rows = mapped_rows_by_table.get("users", []) + mapped_rows_by_table.get("reps", [])
    default_team_id = dataset["teams"][0]["id"]
    for row in user_rows:
        rep_id = _uuid_or_new(row.get("id"))
        team_id = _uuid_or_new(row.get("team_id")) if row.get("team_id") else default_team_id
        if not any(t["id"] == team_id for t in dataset["teams"]):
            team_id = default_team_id
        email = str(row.get("email") or f"rep-{rep_id[:8]}@example.com").lower()
        rep = {
            "id": rep_id,
            "team_id": team_id,
            "name": str(row.get("name") or "Unknown Rep"),
            "email": email,
            "region": str(row.get("region") or "Unknown"),
            "hire_date": str(row.get("hire_date") or date.today().isoformat()),
        }
        dataset["reps"].append(rep)
        reps_by_id[rep_id] = rep_id
        reps_by_email[email] = rep_id

    # accounts
    for row in mapped_rows_by_table.get("accounts", []):
        account_id = _uuid_or_new(row.get("id"))
        dataset["accounts"].append(
            {
                "id": account_id,
                "name": str(row.get("name") or "Unknown Account"),
                "industry": str(row.get("industry") or "Unknown"),
                "employee_count": str(int(_to_float(row.get("employee_count"), 0))),
                "annual_revenue": str(_to_float(row.get("annual_revenue"), 0.0)),
            }
        )
        accounts_by_id[account_id] = account_id

    # opportunities/deals
    opp_rows = mapped_rows_by_table.get("opportunities", []) + mapped_rows_by_table.get("deals", [])
    row_lineage_deals: list[dict[str, Any]] = []
    for row in opp_rows:
        deal_id = _uuid_or_new(row.get("id"))
        rep_id = None
        rep_resolution_method = "unresolved"
        if row.get("owner_user_id"):
            rep_id = reps_by_id.get(_uuid_or_new(row.get("owner_user_id")))
            if rep_id:
                rep_resolution_method = "direct_fk.owner_user_id"
        if row.get("rep_id"):
            rep_id = reps_by_id.get(_uuid_or_new(row.get("rep_id"))) or rep_id
            if rep_id and rep_resolution_method == "unresolved":
                rep_resolution_method = "direct_fk.rep_id"
        if row.get("email") and not rep_id:
            rep_id = reps_by_email.get(str(row.get("email")).lower())
            if rep_id:
                rep_resolution_method = "business_key.email"
        if not rep_id and dataset["reps"]:
            rep_id = dataset["reps"][0]["id"]
            rep_resolution_method = "fallback.first_rep"

        account_id = _uuid_or_new(row.get("account_id")) if row.get("account_id") else ""
        account_resolution_method = "direct_fk.account_id" if account_id else "missing"
        if account_id and account_id not in accounts_by_id:
            # Keep existing behavior: create placeholder account for unresolved refs.
            placeholder_id = account_id
            dataset["accounts"].append(
                {
                    "id": placeholder_id,
                    "name": "Unknown Account",
                    "industry": "Unknown",
                    "employee_count": "0",
                    "annual_revenue": "0.0",
                }
            )
            accounts_by_id[placeholder_id] = placeholder_id
            account_resolution_method = "fallback.placeholder_account"

        created = row.get("created_at") or datetime.now(UTC).isoformat()
        dataset["deals"].append(
            {
                "id": deal_id,
                "account_id": account_id,
                "rep_id": rep_id or "",
                "name": str(row.get("name") or "Imported Deal"),
                "product": str(row.get("product") or "Unknown Product"),
                "stage": str(row.get("stage") or "Qualification"),
                "amount": str(_to_float(row.get("amount"), 0.0)),
                "close_probability": str(int(_to_float(row.get("close_probability"), 30))),
                "expected_close_date": str(row.get("expected_close_date") or row.get("close_date") or ""),
                "actual_close_date": str(row.get("actual_close_date") or ""),
                "created_at": str(created),
            }
        )
        row_lineage_deals.append(
            {
                "deal_id": deal_id,
                "rep_resolution": rep_resolution_method,
                "account_resolution": account_resolution_method,
                "confidence": 0.7 if "fallback" in rep_resolution_method or "fallback" in account_resolution_method else 1.0,
            }
        )

    # quotas
    for row in mapped_rows_by_table.get("quotas", []):
        rep_id = _uuid_or_new(row.get("rep_id")) if row.get("rep_id") else ""
        if rep_id not in reps_by_id:
            if dataset["reps"]:
                rep_id = dataset["reps"][0]["id"]
            else:
                continue
        dataset["quotas"].append(
            {
                "rep_id": rep_id,
                "period": str(row.get("period") or f"{date.today().year}-Q1"),
                "amount": str(_to_float(row.get("amount"), 0.0)),
            }
        )

    # revenue (revenue table or monthly_finance mapped)
    for row in mapped_rows_by_table.get("revenue", []):
        rep_id = _uuid_or_new(row.get("rep_id")) if row.get("rep_id") else ""
        if rep_id not in reps_by_id:
            if dataset["reps"]:
                rep_id = dataset["reps"][0]["id"]
            else:
                continue
        dataset["revenue"].append(
            {
                "rep_id": rep_id,
                "period": str(row.get("period") or date.today().strftime("%Y-%m")),
                "amount": str(_to_float(row.get("amount"), 0.0)),
            }
        )

    # activities
    for row in mapped_rows_by_table.get("activities", []):
        if not dataset["deals"]:
            break
        deal_id = _uuid_or_new(row.get("deal_id")) if row.get("deal_id") else dataset["deals"][0]["id"]
        if not any(d["id"] == deal_id for d in dataset["deals"]):
            deal_id = dataset["deals"][0]["id"]
        rep_id = _uuid_or_new(row.get("rep_id")) if row.get("rep_id") else dataset["reps"][0]["id"] if dataset["reps"] else ""
        dataset["activities"].append(
            {
                "id": str(uuid.uuid4()),
                "deal_id": deal_id,
                "rep_id": rep_id,
                "type": str(row.get("type") or "call"),
                "outcome": str(row.get("outcome") or "neutral"),
                "notes": str(row.get("notes") or "Imported activity"),
                "activity_date": str(row.get("activity_date") or datetime.now(UTC).isoformat()),
            }
        )

    # Keep downstream behavior intact with explicit fallbacks.
    if dataset["reps"] and not dataset["quotas"]:
        p = f"{date.today().year}-Q{((date.today().month - 1)//3)+1}"
        for rep in dataset["reps"]:
            dataset["quotas"].append({"rep_id": rep["id"], "period": p, "amount": "500000"})
        warnings.append("manifest ingestion: generated default quotas")

    if dataset["reps"] and not dataset["revenue"]:
        p = date.today().strftime("%Y-%m")
        for rep in dataset["reps"]:
            dataset["revenue"].append({"rep_id": rep["id"], "period": p, "amount": "0"})
        warnings.append("manifest ingestion: generated default revenue rows")

    metadata = {
        "manifest_name": manifest_name,
        "manifest_version": manifest.version,
        "schema_fingerprint": manifest.schema_fingerprint,
        "mapping_stats": mapping_stats,
        "mapped_tables": sorted(mapped_rows_by_table.keys()),
        "relationship_resolution": relationship_details["relationship_summary"],
        "row_provenance": relationship_details["row_provenance"],
        "canonical_lineage": {
            "deals": row_lineage_deals,
        },
    }

    return dataset, warnings, metadata
