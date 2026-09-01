"""
data_generator.py
=================
Generates realistic synthetic sales data and seeds the PostgreSQL database.
Run:  python -m backend.data_generator
"""
import argparse
import asyncio
import csv
from collections import defaultdict
import json
import random
import re
import uuid
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from faker import Faker
from backend.database import get_session_factory, get_engine, Base
from backend.tenancy import tenant_scope
from backend.ingestion.ingestion_run import IngestionRun
from backend.models import (
    Team,
    Rep,
    Quota,
    Account,
    Deal,
    Activity,
    Revenue,
    Position,
    UserProfile,
    Manager,
    Plan,
    Rule,
    Territory,
    PlanAssignment,
    UserTerritoryAssignment,
    Product,
    RepProductAssignment,
    SalesUnit,
    SalesUnitLineItem,
    SalesCredit,
    PayoutRecord,
    Booking,
    ChurnEvent,
    ArrWaterfallEntry,
    AttainmentSnapshot,
    RepRamp,
    Lead,
    Opportunity,
)
from backend.validation.revops_rules import RevOpsBusinessRuleValidator

fake = Faker()
random.seed(42)


def _company_email_domain(company_name: str) -> str:
    """Derive an email domain from company name: 'Evolve Tech' → 'evolvetech.com'."""
    slug = re.sub(r"[^a-zA-Z0-9]+", "", company_name.strip().lower())
    return f"{slug}.com"


def _make_rep_email(full_name: str, domain: str, seen_emails: set[str]) -> str:
    """Generate firstname.lastname@domain.com, deduplicating with a counter suffix."""
    parts = full_name.strip().lower().split()
    base = f"{parts[0]}.{parts[-1]}" if len(parts) >= 2 else parts[0]
    base = re.sub(r"[^a-z0-9.]", "", base)
    email = f"{base}@{domain}"
    counter = 1
    while email in seen_emails:
        email = f"{base}{counter}@{domain}"
        counter += 1
    seen_emails.add(email)
    return email

REGIONS     = ["West", "East", "Central", "APAC", "EMEA"]
INDUSTRIES  = ["SaaS", "Healthcare", "Finance", "Retail", "Manufacturing", "Media", "Logistics"]
PRODUCTS    = ["Enterprise Suite", "Pro Platform", "Growth Package", "Starter Plan", "Data Add-on", "AI Insights"]

# Insurance-specific product catalog (used when archetype == "insurance")
INSURANCE_PRODUCTS = [
    "Term Life Policy",
    "Whole Life Policy",
    "Group Health Plan",
    "Property & Casualty",
    "Commercial Lines",
    "Workers Compensation",
    "Fixed Annuity",
    "Medicare Supplement",
    "Long-Term Care",
    "Group Benefits Suite",
]

# Insurance-specific territory names (used when archetype == "insurance")
INSURANCE_TERRITORY_NAMES = [
    "Southeast Region",
    "Northeast Region",
    "Midwest Region",
    "Southwest Region",
    "Northwest Region",
    "Central Region",
]
STAGES      = ["Prospecting", "Qualification", "Proposal", "Negotiation", "Closed Won", "Closed Lost"]
STAGE_PROB  = {        # base close probability by stage
    "Prospecting": 10, "Qualification": 25, "Proposal": 45,
    "Negotiation": 75, "Closed Won": 100,  "Closed Lost": 0
}
ACT_TYPES   = ["call", "email", "meeting", "demo", "follow_up"]
ACT_OUTCOMES = ["positive", "neutral", "negative", "no_response"]
TABLE_ORDER = ["teams", "reps", "quotas", "accounts", "deals", "activities", "revenue"]
EXTENSION_TABLE_ORDER = [
    "products",
    "territories",
    "rep_hierarchy",
    "positions",
    "users",
    "managers",
    "plans",
    "rules",
    "plan_assignments",
    "rep_product_assignments",
    "user_territory_assignments",
    "rep_ramp",
    "bookings",
    "churn_events",
    "arr_waterfall",
    "sales_units",
    "sales_credits",
    "attainment_snapshots",
    "leads",           # B3
    "opportunities",   # B3
]

# Credit split profiles: role → (credit_type, credit_percent range)
CREDIT_SPLIT_PROFILES: dict[str, list[tuple[str, float, float]]] = {
    "saas_enterprise": [
        ("primary_ae", 0.70, 0.80),
        ("sdr_sourced", 0.10, 0.20),
        ("overlay_specialist", 0.05, 0.15),
    ],
    "saas_smb": [
        ("primary_ae", 0.90, 1.00),
    ],
    "field_sales": [
        ("primary_ae", 0.75, 0.90),
        ("sdr_sourced", 0.10, 0.20),
    ],
    "overlay_specialist": [
        ("primary_ae", 0.50, 0.65),
        ("overlay_specialist", 0.25, 0.40),
        ("sdr_sourced", 0.10, 0.15),
    ],
    "insurance": [
        ("primary_ae", 0.70, 0.85),
        ("sdr_sourced", 0.10, 0.20),
        ("overlay_specialist", 0.05, 0.10),
    ],
}

# Revenue type semantics for ARR/MRR decomposition
REVENUE_TYPES = ["new_logo", "expansion", "contraction", "churn", "renewal"]

# Archetype profiles — control deal size, cycle time, quota target multiplier, product mix
ARCHETYPE_PROFILES: dict[str, dict] = {
    "saas_enterprise": {
        "deal_size_range": (50_000, 500_000),
        "cycle_days_range": (60, 180),
        "quota_growth_factor": 1.20,
        "win_rate_weight": [12, 18, 22, 18, 22, 8],   # STAGES weights
        "primary_revenue_types": ["new_logo", "expansion", "renewal"],
        "churn_rate": 0.05,
        "expansion_rate": 0.25,
        "description": "Enterprise ACV plan — large deals, long cycles, high expansion",
        "base_annual_quota_ic": 200_000.0,
        "min_deals_won_per_rep": 4,
        "seasonal_quarterly": {1: 0.80, 2: 0.95, 3: 1.10, 4: 1.15},
    },
    "saas_smb": {
        "deal_size_range": (5_000, 60_000),
        "cycle_days_range": (14, 60),
        "quota_growth_factor": 1.15,
        "win_rate_weight": [10, 20, 25, 20, 18, 7],
        "primary_revenue_types": ["new_logo", "renewal", "churn"],
        "churn_rate": 0.12,
        "expansion_rate": 0.10,
        "description": "SMB velocity plan — small deals, fast cycles, higher churn",
        "base_annual_quota_ic": 80_000.0,
        "min_deals_won_per_rep": 6,
        "seasonal_quarterly": {1: 0.85, 2: 0.95, 3: 1.05, 4: 1.15},
    },
    "field_sales": {
        "deal_size_range": (20_000, 250_000),
        "cycle_days_range": (30, 120),
        "quota_growth_factor": 1.10,
        "win_rate_weight": [15, 20, 20, 15, 20, 10],
        "primary_revenue_types": ["new_logo", "expansion", "renewal"],
        "churn_rate": 0.08,
        "expansion_rate": 0.15,
        "description": "Field sales — territory-based, balanced deal mix",
        "base_annual_quota_ic": 160_000.0,
        "min_deals_won_per_rep": 4,
        "seasonal_quarterly": {1: 0.80, 2: 0.95, 3: 1.10, 4: 1.15},
    },
    "overlay_specialist": {
        "deal_size_range": (80_000, 600_000),
        "cycle_days_range": (45, 150),
        "quota_growth_factor": 1.25,
        "win_rate_weight": [8, 15, 25, 22, 25, 5],
        "primary_revenue_types": ["expansion", "new_logo"],
        "churn_rate": 0.03,
        "expansion_rate": 0.40,
        "description": "Overlay / expansion specialist — upsell focus, accelerator-heavy",
        "base_annual_quota_ic": 280_000.0,
        "min_deals_won_per_rep": 3,
        "seasonal_quarterly": {1: 0.78, 2: 0.95, 3: 1.12, 4: 1.15},
    },
    "insurance": {
        "deal_size_range": (15_000, 300_000),
        "cycle_days_range": (30, 120),
        "quota_growth_factor": 1.12,
        "win_rate_weight": [12, 20, 22, 18, 16, 12],
        "primary_revenue_types": ["new_logo", "renewal", "expansion"],
        "churn_rate": 0.06,
        "expansion_rate": 0.18,
        "description": "Insurance carrier/agency — premium-based, renewal-heavy, territory coverage",
        "base_annual_quota_ic": 150_000.0,
        "min_deals_won_per_rep": 3,
        "seasonal_quarterly": {1: 0.82, 2: 0.95, 3: 1.05, 4: 1.18},
    },
}

# Ramp schedule: months_since_hire → ramp_factor (fraction of full quota)
RAMP_SCHEDULE = {0: 0.25, 1: 0.35, 2: 0.50, 3: 0.65, 4: 0.75, 5: 0.85, 6: 1.00}

# A9 — Seasonal deal creation multipliers by calendar month
MONTH_MULTIPLIERS = {
    1: 0.70, 2: 0.80, 3: 1.40, 4: 0.90, 5: 1.00,
    6: 1.30, 7: 0.75, 8: 0.85, 9: 1.20, 10: 1.10,
    11: 1.20, 12: 1.60,
}

# A7 — Role-stratified deal weight: ICs carry most of the pipeline
ROLE_DEAL_WEIGHTS: dict[str, float] = {
    "Chief Revenue Officer": 0.5,
    "SVP Sales": 0.8,
    "VP Sales": 0.9,
    "Director of Sales": 1.0,
    "Sales Manager": 1.5,
    "Senior Account Executive": 7.0,
    "Account Executive": 7.0,
    "Overlay Specialist": 7.0,
    "Sales Development Representative": 2.0,
}

# A2 — Quota rank multipliers; CRO=0 means no individual quota assigned
QUOTA_RANK_MULTIPLIERS: dict[str, float] = {
    "Chief Revenue Officer": 0.0,
    "SVP Sales": 1.5,
    "VP Sales": 1.4,
    "Director of Sales": 1.25,
    "Sales Manager": 0.40,
    "Senior Account Executive": 1.0,
    "Account Executive": 1.0,
    "Overlay Specialist": 0.9,
    "Sales Development Representative": 0.25,
}

BASE_ANNUAL_QUOTA = 200_000.0  # baseline annual quota for a fully-ramped IC


def _ramp_factor(hire_date: date, period_date: date) -> float:
    """Return quota ramp factor (0.25-1.0) based on months since hire at period start."""
    months = (period_date.year - hire_date.year) * 12 + (period_date.month - hire_date.month)
    if months < 0:
        return 0.25
    return RAMP_SCHEDULE.get(min(months, 6), 1.00)


def _formula_quota(
    rep_historical_revenue: list[float],
    growth_factor: float,
    ramp_factor_val: float,
) -> float:
    """Set quota using p70 of historical rep revenue × growth factor × ramp."""
    if not rep_historical_revenue:
        base = 150_000.0
    else:
        sorted_rev = sorted(rep_historical_revenue)
        idx = int(len(sorted_rev) * 0.70)
        base = sorted_rev[min(idx, len(sorted_rev) - 1)]
    quarterly = base * 3 * growth_factor * ramp_factor_val
    return round(max(quarterly, 50_000.0), 2)


def _write_rows_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames = list(rows[0].keys())
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _scale_revenue_to_target(dataset: dict[str, list[dict]], target_total_revenue: float | None) -> None:
    if not target_total_revenue or target_total_revenue <= 0:
        return
    revenue_rows = dataset.get("revenue", [])
    if not revenue_rows:
        return
    current_total = sum(float(r.get("amount", 0) or 0) for r in revenue_rows)
    if current_total <= 0:
        # Distribute evenly if all rows are zero.
        per_row = round(target_total_revenue / max(1, len(revenue_rows)), 2)
        for row in revenue_rows:
            row["amount"] = str(per_row)
        return
    scale = target_total_revenue / current_total
    for row in revenue_rows:
        row["amount"] = str(round(float(row.get("amount", 0) or 0) * scale, 2))


def _build_saas_extension_tables(
    dataset: dict[str, list[dict]],
    n_plans: int,
    n_rules: int,
    n_products: int,
    n_territories: int,
    n_subregions_per_territory: int,
    include_org_hierarchy: bool,
    archetype: str,
) -> dict[str, list[dict]]:
    reps = dataset.get("reps", [])
    rep_ids = [r["id"] for r in reps]
    rep_by_id = {r["id"]: r for r in reps}

    # Products (explicit catalog for SaaS/Insurance profile)
    product_catalog = INSURANCE_PRODUCTS if archetype == "insurance" else PRODUCTS
    product_rows: list[dict] = []
    for i in range(n_products):
        product_rows.append(
            {
                "id": str(uuid.uuid4()),
                "external_id": f"PROD-{i+1:03d}",
                "product_sku": f"{'INS' if archetype == 'insurance' else 'SAAS'}-SKU-{i+1:03d}",
                "name": product_catalog[i % len(product_catalog)] if i < len(product_catalog) else f"{'Insurance' if archetype == 'insurance' else 'SaaS'} Product {i+1}",
                "category": "Insurance" if archetype == "insurance" else "SaaS",
                "source_system": "generated",
                "created_at": datetime.now(UTC).isoformat(),
            }
        )

    # Territories + subregions (insurance uses region-specific names)
    territory_rows: list[dict] = []
    if archetype == "insurance":
        territory_name_pool = INSURANCE_TERRITORY_NAMES
    else:
        territory_name_pool = ["North America", "EMEA", "APAC", "LATAM"]
    for i in range(n_territories):
        parent_id = str(uuid.uuid4())
        parent_name = territory_name_pool[i] if i < len(territory_name_pool) else f"Territory {i+1}"
        territory_rows.append(
            {
                "id": parent_id,
                "external_id": f"TERR-{i+1:03d}",
                "territory_code": f"T-{i+1:03d}",
                "name": parent_name,
                "parent_territory_id": "",
                "region": parent_name,
                "segment": "Enterprise",
                "source_system": "generated",
                "created_at": datetime.now(UTC).isoformat(),
                "effective_start_date": date.today().replace(year=date.today().year - 2).isoformat(),
                "effective_end_date": "",
            }
        )
        for j in range(n_subregions_per_territory):
            territory_rows.append(
                {
                    "id": str(uuid.uuid4()),
                    "external_id": f"SUBTERR-{i+1:03d}-{j+1:02d}",
                    "territory_code": f"T-{i+1:03d}-S{j+1:02d}",
                    "name": f"{parent_name} Subregion {j+1}",
                    "parent_territory_id": parent_id,
                    "region": parent_name,
                    "segment": random.choice(["Mid-Market", "Enterprise", "SMB"]),
                    "source_system": "generated",
                    "created_at": datetime.now(UTC).isoformat(),
                    "effective_start_date": date.today().replace(year=date.today().year - 2).isoformat(),
                    "effective_end_date": "",
                }
            )

    # Org hierarchy (rep to manager chain) plus enterprise user/position tables.
    hierarchy_rows: list[dict] = []
    positions_rows: list[dict] = []
    users_rows: list[dict] = []
    managers_rows: list[dict] = []

    role_definitions = [
        ("Chief Revenue Officer", "Executive", "L0"),
        ("SVP Sales", "Senior Leadership", "L1"),
        ("VP Sales", "Leadership", "L2"),
        ("Director of Sales", "Leadership", "L3"),
        ("Sales Manager", "Management", "L4"),
        ("Overlay Specialist", "Individual Contributor", "L5"),
        ("Senior Account Executive", "Individual Contributor", "L5"),
        ("Account Executive", "Individual Contributor", "L5"),
        ("Sales Development Representative", "Individual Contributor", "L5"),
    ]
    role_to_position_id: dict[str, str] = {}
    for idx, (role_name, level_name, _level_code) in enumerate(role_definitions, start=1):
        position_id = str(uuid.uuid4())
        role_to_position_id[role_name] = position_id
        _rank = _infer_position_rank(role_name)
        positions_rows.append(
            {
                "id": position_id,
                "external_id": f"POS-{idx:03d}",
                "name": role_name,
                "level": level_name,
                "rank": _rank,
                "rank_label": _RANK_LABELS.get(_rank, "Unknown"),
                "source_system": "generated",
                "created_at": datetime.now(UTC).isoformat(),
                "effective_start_date": date.today().replace(year=date.today().year - 2).isoformat(),
                "effective_end_date": "",
            }
        )

    if include_org_hierarchy and rep_ids:
        # Archetype-specific manager span and IC role mix.
        manager_span_cap = {
            "saas_smb": 8,
            "field_sales": 6,
            "saas_enterprise": 5,
            "overlay_specialist": 4,
        }.get(archetype, 6)
        ic_role_mix = {
            "saas_smb": ["Account Executive", "Account Executive", "Sales Development Representative", "Sales Development Representative", "Senior Account Executive"],
            "field_sales": ["Senior Account Executive", "Account Executive", "Account Executive", "Account Executive", "Sales Development Representative"],
            "saas_enterprise": ["Senior Account Executive", "Senior Account Executive", "Account Executive", "Account Executive", "Sales Development Representative"],
            "overlay_specialist": ["Overlay Specialist", "Overlay Specialist", "Senior Account Executive", "Account Executive", "Sales Development Representative"],
        }.get(archetype, ["Account Executive", "Senior Account Executive", "Sales Development Representative"])

        n = len(rep_ids)
        cro_count = 1
        # Use flatter hierarchy for smaller companies — SVP/VP/Director only for larger orgs
        svp_count = 1 if n >= 30 else 0
        vp_count = 1 if n >= 25 else 0
        director_count = 1 if n >= 20 else 0

        base_leadership = cro_count + svp_count + vp_count + director_count
        remaining = max(0, n - base_leadership)
        manager_count = 0
        if remaining > 0:
            manager_count = max(1, (remaining + manager_span_cap - 1) // manager_span_cap)
            manager_count = min(manager_count, max(1, n // 3))
        while base_leadership + manager_count > n:
            manager_count -= 1

        idx = 0
        cro_ids = rep_ids[idx: idx + cro_count]
        idx += cro_count
        svp_ids = rep_ids[idx: idx + svp_count]
        idx += svp_count
        vp_ids = rep_ids[idx: idx + vp_count]
        idx += vp_count
        director_ids = rep_ids[idx: idx + director_count]
        idx += director_count
        manager_ids = rep_ids[idx: idx + manager_count]
        idx += manager_count
        ic_ids = rep_ids[idx:]

        def _append_hierarchy_row(rep_id: str, manager_id: str, role: str, level: str) -> None:
            hierarchy_rows.append(
                {
                    "rep_id": rep_id,
                    "manager_rep_id": manager_id,
                    "role": role,
                    "level": level,
                    "effective_start_date": date.today().replace(year=date.today().year - 2).isoformat(),
                    "effective_end_date": "",
                    "source_system": "generated",
                }
            )

        if cro_ids:
            _append_hierarchy_row(cro_ids[0], "", "Chief Revenue Officer", "L0")
        for rep_id in svp_ids:
            _append_hierarchy_row(rep_id, cro_ids[0] if cro_ids else "", "SVP Sales", "L1")
        for rep_id in vp_ids:
            upstream = svp_ids[0] if svp_ids else (cro_ids[0] if cro_ids else "")
            _append_hierarchy_row(rep_id, upstream, "VP Sales", "L2")
        for rep_id in director_ids:
            upstream = vp_ids[0] if vp_ids else (svp_ids[0] if svp_ids else (cro_ids[0] if cro_ids else ""))
            _append_hierarchy_row(rep_id, upstream, "Director of Sales", "L3")
        for rep_id in manager_ids:
            upstream_pool = director_ids or vp_ids or svp_ids or cro_ids
            upstream = random.choice(upstream_pool) if upstream_pool else ""
            _append_hierarchy_row(rep_id, upstream, "Sales Manager", "L4")

        # Enforce manager span with round-robin IC assignment.
        ic_manager_pool = manager_ids or director_ids or vp_ids or svp_ids or cro_ids
        for i, rep_id in enumerate(ic_ids):
            manager_id = ic_manager_pool[i % len(ic_manager_pool)] if ic_manager_pool else ""
            role = random.choice(ic_role_mix)
            _append_hierarchy_row(rep_id, manager_id, role, "L5")

    for rep in reps:
        role_row = next((r for r in hierarchy_rows if r["rep_id"] == rep["id"]), None)
        role_name = role_row["role"] if role_row else "Account Executive"
        user_id = str(uuid.uuid4())
        users_rows.append(
            {
                "id": user_id,
                "external_id": f"USR-{rep['id'][:8]}",
                "position_id": role_to_position_id.get(role_name, role_to_position_id["Account Executive"]),
                "team_id": rep.get("team_id", ""),
                "name": rep.get("name", "Unknown Rep"),
                "email": rep.get("email", f"{rep['id']}@generated.local"),
                "region": rep.get("region", ""),
                "hire_date": rep.get("hire_date", ""),
                "source_system": "generated",
                "mapping_basis": "rep-mirror",
                "evidence_score": "0.99",
                "created_at": datetime.now(UTC).isoformat(),
                "effective_start_date": date.today().replace(year=date.today().year - 2).isoformat(),
                "effective_end_date": "",
            }
        )

    rep_to_user = {rep["id"]: user["id"] for rep, user in zip(reps, users_rows)}
    for hr in hierarchy_rows:
        manager_rep_id = hr.get("manager_rep_id", "")
        managers_rows.append(
            {
                "id": str(uuid.uuid4()),
                "user_id": rep_to_user.get(hr["rep_id"], ""),
                "manager_user_id": rep_to_user.get(manager_rep_id, "") if manager_rep_id else "",
                "source_system": "generated",
                "created_at": datetime.now(UTC).isoformat(),
            }
        )

    # Plans + Rules (versioned: one plan per fiscal year so history is fully covered)
    plan_rows: list[dict] = []
    current_year = date.today().year
    plan_descriptions_insurance = [
        "Annual premium growth and new policy plan",
        "Renewal retention and cross-sell plan",
        "Commercial lines expansion plan",
        "Territory penetration and new agency plan",
        "Group benefits and employer market plan",
    ]
    plan_descriptions_saas = [
        "New logo + expansion blended plan",
        "Enterprise annual contract value plan",
        "Mid-market growth and retention plan",
        "Overlay specialist accelerator plan",
        "SMB velocity and churn reduction plan",
    ]
    plan_desc_pool = plan_descriptions_insurance if archetype == "insurance" else plan_descriptions_saas
    for i in range(n_plans):
        fy_year = current_year - (n_plans - 1 - i)   # oldest → newest
        fy_start = date(fy_year, 1, 1)
        fy_end = date(fy_year, 12, 31) if fy_year < current_year else None  # current year stays open
        plan_rows.append(
            {
                "id": str(uuid.uuid4()),
                "external_id": f"PLAN-{i+1:03d}",
                "name": f"FY{fy_year} {'Insurance' if archetype == 'insurance' else 'Sales'} Plan {i+1}",
                "description": plan_desc_pool[i % len(plan_desc_pool)],
                "source_system": "generated",
                "created_at": datetime.now(UTC).isoformat(),
                "effective_start_date": fy_start.isoformat(),
                "effective_end_date": fy_end.isoformat() if fy_end else "",
            }
        )

    # Generate a complete rule set for every plan.
    # Insurance archetype: 6 tiers with rollup and carryover semantics.
    # Other archetypes: 4 standard tiers.
    _TIER_DEFS_STANDARD = [
        (0,   79.99,  0.03, 0.00,    0, False, False, 0.0, 0.0),
        (80,  99.99,  0.05, 0.00,    0, False, False, 0.0, 0.0),
        (100, 119.99, 0.08, 0.01, 2000, False,  True, 0.0, 5000.0),
        (120, 999.0,  0.10, 0.02, 2000,  True,  True, 0.15, 10000.0),
    ]
    _TIER_DEFS_INSURANCE = [
        (0,   69.99,  0.020, 0.000,    0, False, False, 0.0, 0.0),
        (70,  84.99,  0.040, 0.000,    0, False, False, 0.0, 0.0),
        (85,  99.99,  0.060, 0.000,    0, False, False, 0.0, 0.0),
        (100, 114.99, 0.090, 0.010, 1500, False,  True, 0.0, 5000.0),
        (115, 129.99, 0.110, 0.020, 2500,  True,  True, 0.20, 8000.0),
        (130, 999.0,  0.130, 0.030, 3500,  True,  True, 0.25, 12000.0),
    ]
    _TIER_DEFS = _TIER_DEFS_INSURANCE if archetype == "insurance" else _TIER_DEFS_STANDARD
    rule_rows: list[dict] = []
    _rule_idx = 0
    for plan in plan_rows:
        for tier_num, (t_min, t_max, rate, accel, bonus, rollup_en, carryover_en, rollup_pct, carryover_cap) in enumerate(_TIER_DEFS, start=1):
            _rule_idx += 1
            rule_rows.append(
                {
                    "id": str(uuid.uuid4()),
                    "plan_id": plan["id"],
                    "name": f"Tier {tier_num} Rule",
                    "metric_name": "attainment_pct",
                    "threshold_min": str(t_min),
                    "threshold_max": str(t_max),
                    "rate": str(rate),
                    "accelerator_rate": str(accel),
                    "bonus_amount": str(bonus),
                    # Rollup: carry excess attainment from this period into next
                    "rollup_enabled": "true" if rollup_en else "false",
                    "rollup_pct": str(rollup_pct),
                    # Carryover: uncredited attainment above cap rolls to next period
                    "carryover_enabled": "true" if carryover_en else "false",
                    "carryover_cap": str(carryover_cap),
                    "source_system": "generated",
                    "created_at": datetime.now(UTC).isoformat(),
                }
            )

    # Plan assignments: assign quota-carrying sellers and managers to plans.
    plan_assignments_rows: list[dict] = []
    quota_roles = {
        "Senior Account Executive",
        "Account Executive",
        "Sales Development Representative",
        "Overlay Specialist",
        "Sales Manager",
        "Director of Sales",
    }
    role_by_rep = {r["rep_id"]: r["role"] for r in hierarchy_rows}
    for rep in reps:
        role = role_by_rep.get(rep["id"], "Account Executive")
        if role not in quota_roles or not plan_rows:
            continue
        # Prefer the most recent (current/open) plan instead of random selection
        # so plan effective dates align with the data period.
        current_year_plans = [p for p in plan_rows if not p.get("effective_end_date")]
        latest_plans = current_year_plans or sorted(plan_rows, key=lambda p: p.get("effective_start_date", ""), reverse=True)[:1]
        selected_plan = random.choice(latest_plans) if latest_plans else random.choice(plan_rows)
        plan_assignments_rows.append(
            {
                "id": str(uuid.uuid4()),
                "user_id": rep_to_user.get(rep["id"], ""),
                "plan_id": selected_plan["id"],
                "effective_start_date": date.today().replace(month=1, day=1).isoformat(),
                "effective_end_date": "",
                "source_system": "generated",
                "mapping_basis": "role-based",
                "evidence_score": "0.95",
                "created_at": datetime.now(UTC).isoformat(),
            }
        )

    # Rep product assignments: each rep is assigned a subset of products based on role.
    # Managers/execs get flagship products; ICs get a territory-aligned mix; overlays get expansion SKUs.
    rep_product_assignments_rows: list[dict] = []
    if product_rows:
        flagship_products = product_rows[:min(3, len(product_rows))]  # first 3 = flagship
        expansion_products = product_rows[3:min(6, len(product_rows))] or flagship_products
        all_products = product_rows

        leadership_roles = {"Chief Revenue Officer", "SVP Sales", "VP Sales", "Director of Sales"}
        manager_roles = {"Sales Manager"}
        expansion_roles = {"Overlay Specialist"}

        seen_rep_product: set[tuple[str, str]] = set()

        for rep in reps:
            role = role_by_rep.get(rep["id"], "Account Executive")

            if role in leadership_roles:
                assigned = flagship_products[:2]
                specialization = "oversight"
            elif role in manager_roles:
                assigned = flagship_products
                specialization = "primary_seller"
            elif role in expansion_roles:
                assigned = expansion_products + flagship_products[:1]
                specialization = "expansion"
            else:
                # ICs: 3-4 products, rotated by rep index for variety
                rep_idx = reps.index(rep)
                start = (rep_idx * 3) % len(all_products)
                assigned = [all_products[(start + k) % len(all_products)] for k in range(min(4, len(all_products)))]
                specialization = "primary_seller"

            for i, prod in enumerate(assigned):
                key = (rep["id"], prod["id"])
                if key in seen_rep_product:
                    continue
                seen_rep_product.add(key)
                rep_product_assignments_rows.append({
                    "id": str(uuid.uuid4()),
                    "rep_id": rep["id"],
                    "product_id": prod["id"],
                    "product_name": prod["name"],
                    "product_sku": prod.get("product_sku", ""),
                    "is_primary": "true" if i == 0 else "false",
                    "specialization": specialization,
                    "effective_start_date": date.today().replace(month=1, day=1).isoformat(),
                    "effective_end_date": "",
                    "source_system": "generated",
                    "created_at": datetime.now(UTC).isoformat(),
                })

    # User territory assignments: one primary territory per user.
    user_territory_assignments_rows: list[dict] = []
    leaf_territories = [t for t in territory_rows if t.get("parent_territory_id")]
    candidate_territories = leaf_territories or territory_rows
    for rep in reps:
        if not candidate_territories:
            break
        terr = random.choice(candidate_territories)
        user_territory_assignments_rows.append(
            {
                "id": str(uuid.uuid4()),
                "user_id": rep_to_user.get(rep["id"], ""),
                "territory_id": terr["id"],
                "is_primary": "true",
                "source_system": "generated",
                "mapping_basis": "regional-alignment",
                "evidence_score": "0.92",
                "created_at": datetime.now(UTC).isoformat(),
                "effective_start_date": date.today().replace(year=date.today().year - 1).isoformat(),
                "effective_end_date": "",
            }
        )

    # Rep ramp schedule (one row per rep per period)
    rep_ramp_rows: list[dict] = []
    revenue_rows = dataset.get("revenue", [])
    # Build a quick lookup: rep_id → sorted monthly amounts (oldest first)
    rev_by_rep: dict[str, list[float]] = {}
    for rv in revenue_rows:
        rev_by_rep.setdefault(rv["rep_id"], []).append(float(rv.get("amount", 0) or 0))
    for rep in reps:
        hire_date_str = rep.get("hire_date", "")
        try:
            hire_date = date.fromisoformat(hire_date_str)
        except (ValueError, TypeError):
            hire_date = date.today().replace(year=date.today().year - 3)
        # Use the last 6 quarters as periods for ramp tracking
        today_ref = date.today()
        for m in range(12):  # track ramp over 12 months
            period_date = _month_start_n_months_ago(today_ref, m)
            rf = _ramp_factor(hire_date, period_date)
            hist = rev_by_rep.get(rep["id"], [])
            full_quota = _formula_quota(hist, 1.10, 1.00)
            rep_ramp_rows.append({
                "rep_id": rep["id"],
                "period": period_date.strftime("%Y-%m"),
                "months_since_hire": max(0, (period_date.year - hire_date.year) * 12 + (period_date.month - hire_date.month)),
                "ramp_factor": str(rf),
                "quota_at_ramp": str(round(full_quota * rf, 2)),
                "full_quota": str(full_quota),
                "is_ramping": "true" if rf < 1.0 else "false",
            })

    # Bookings table (one row per Closed Won deal)
    deal_rows = dataset.get("deals", [])
    booking_rows: list[dict] = []
    for deal in deal_rows:
        if deal.get("stage") != "Closed Won":
            continue
        amount = float(deal.get("amount", 0) or 0)
        # Assign contract term based on deal size (larger = longer term)
        if amount >= 200_000:
            contract_term = 36
        elif amount >= 80_000:
            contract_term = 24
        else:
            contract_term = 12
        booking_date = deal.get("actual_close_date") or deal.get("expected_close_date", date.today().isoformat())
        booking_rows.append({
            "booking_id": str(uuid.uuid4()),
            "deal_id": deal["id"],
            "rep_id": deal["rep_id"],
            "account_id": deal["account_id"],
            "booking_date": booking_date,
            "amount": deal["amount"],
            "arr": str(round(amount / (contract_term / 12), 2)),
            "mrr": str(round(amount / contract_term, 2)),
            "product_sku": f"SKU-{deal.get('product', 'unknown').upper().replace(' ', '-')[:12]}",
            "contract_term_months": str(contract_term),
            "revenue_type": random.choice(["new_logo", "expansion", "renewal"]),
            "recognition_start_date": booking_date,
        })

    # Churn events (one per account per period where ARR decreases)
    account_rows = dataset.get("accounts", [])
    churn_event_rows: list[dict] = []
    today_ref = date.today()
    for account in account_rows[:max(1, len(account_rows) // 10)]:  # ~10% of accounts churn
        period_date = _month_start_n_months_ago(today_ref, random.randint(1, 12))
        arr_change = round(random.uniform(-200_000, -5_000), 2)
        event_type = "full_churn" if random.random() < 0.3 else "partial_contraction"
        churn_event_rows.append({
            "event_id": str(uuid.uuid4()),
            "account_id": account["id"],
            "period": period_date.strftime("%Y-%m"),
            "event_type": event_type,
            "arr_change": str(arr_change),
            "reason": random.choice(["price_sensitivity", "competitor", "budget_cut", "product_gap", "low_adoption"]),
            "detected_at": datetime.now(UTC).isoformat(),
        })

    # ARR waterfall: derive component MRR from generated recognized revenue and churn events.
    arr_waterfall_rows: list[dict] = []
    typed_mrr: dict[tuple[str, str], dict[str, float]] = {}
    for rv in revenue_rows:
        rep_id = rv.get("rep_id", "")
        period = rv.get("period", "")
        if not rep_id or not period:
            continue
        typed_mrr.setdefault((rep_id, period), {"new_logo": 0.0, "expansion": 0.0, "contraction": 0.0, "churn": 0.0, "renewal": 0.0})
        rev_type = rv.get("revenue_type", "renewal")
        amount = float(rv.get("amount", 0) or 0)
        mrr = amount / 12
        if rev_type in {"contraction", "churn"}:
            typed_mrr[(rep_id, period)][rev_type] += -abs(mrr)
        elif rev_type in typed_mrr[(rep_id, period)]:
            typed_mrr[(rep_id, period)][rev_type] += mrr
        else:
            typed_mrr[(rep_id, period)]["renewal"] += mrr

    # Apply churn events to mapped reps for explicit negative ARR movement.
    account_to_rep: dict[str, str] = {}
    for d in deal_rows:
        acct = d.get("account_id", "")
        rep_id = d.get("rep_id", "")
        if acct and rep_id and acct not in account_to_rep:
            account_to_rep[acct] = rep_id
    fallback_rep_ids = [r["id"] for r in reps]
    for evt in churn_event_rows:
        period = evt.get("period", "")
        acct = evt.get("account_id", "")
        rep_id = account_to_rep.get(acct, random.choice(fallback_rep_ids) if fallback_rep_ids else "")
        if not rep_id or not period:
            continue
        typed_mrr.setdefault((rep_id, period), {"new_logo": 0.0, "expansion": 0.0, "contraction": 0.0, "churn": 0.0, "renewal": 0.0})
        delta_mrr = float(evt.get("arr_change", 0) or 0) / 12
        evt_type = evt.get("event_type", "")
        if evt_type == "full_churn":
            typed_mrr[(rep_id, period)]["churn"] += delta_mrr
        else:
            typed_mrr[(rep_id, period)]["contraction"] += delta_mrr

    periods = sorted({rv.get("period", "") for rv in revenue_rows if rv.get("period")})
    for rep in reps:
        rep_id = rep["id"]
        hist = rev_by_rep.get(rep_id, [])
        running_arr = (sum(hist) / max(len(hist), 1)) * 12 if hist else 120_000.0
        for period in periods:
            by_type = typed_mrr.get((rep_id, period), {"new_logo": 0.0, "expansion": 0.0, "contraction": 0.0, "churn": 0.0, "renewal": 0.0})
            mrr_new = round(float(by_type.get("new_logo", 0.0)), 2)
            mrr_expansion = round(float(by_type.get("expansion", 0.0)), 2)
            mrr_contraction = round(float(by_type.get("contraction", 0.0)), 2)
            mrr_churn = round(float(by_type.get("churn", 0.0)), 2)
            mrr_renewal = round(float(by_type.get("renewal", 0.0)), 2)
            arr_start = round(running_arr, 2)
            mrr_net = round(mrr_new + mrr_expansion + mrr_contraction + mrr_churn + mrr_renewal, 2)
            running_arr = max(0.0, running_arr + (mrr_new + mrr_expansion + mrr_contraction + mrr_churn) * 12)
            arr_end = round(running_arr, 2)
            arr_waterfall_rows.append(
                {
                    "rep_id": rep_id,
                    "period": period,
                    "mrr_new": str(mrr_new),
                    "mrr_expansion": str(mrr_expansion),
                    "mrr_contraction": str(mrr_contraction),
                    "mrr_churn": str(mrr_churn),
                    "mrr_renewal": str(mrr_renewal),
                    "mrr_net": str(mrr_net),
                    "arr_start": str(arr_start),
                    "arr_end": str(arr_end),
                }
            )

    # SalesUnit rows — one per Closed Won deal
    sales_unit_rows: list[dict] = []
    deal_to_sales_unit: dict[str, str] = {}  # deal_id → sales_unit_id
    for deal in deal_rows:
        if deal.get("stage") != "Closed Won":
            continue
        close_date = deal.get("actual_close_date") or deal.get("expected_close_date", date.today().isoformat())
        su_id = str(uuid.uuid4())
        deal_to_sales_unit[deal["id"]] = su_id
        sales_unit_rows.append({
            "id": su_id,
            "external_id": f"SU-{deal['id'][:8]}",
            "opportunity_id": deal["id"],
            "account_id": deal.get("account_id", ""),
            "owner_user_id": rep_to_user.get(deal.get("rep_id", ""), ""),
            "booked_date": close_date[:10] if close_date else "",
            "amount": deal.get("amount", "0"),
            "currency": "USD",
            "source_system": "generated",
            "created_at": datetime.now(UTC).isoformat(),
        })

    # SalesCredit rows — AE/SDR/overlay split per Closed Won deal
    sales_credit_rows: list[dict] = []
    credit_split_def = CREDIT_SPLIT_PROFILES.get(archetype, CREDIT_SPLIT_PROFILES["saas_smb"])
    all_user_ids_list = [u["id"] for u in users_rows]
    for deal in deal_rows:
        if deal.get("stage") != "Closed Won":
            continue
        su_id = deal_to_sales_unit.get(deal["id"])
        if not su_id:
            continue
        deal_amount = float(deal.get("amount", 0) or 0)
        primary_user_id = rep_to_user.get(deal.get("rep_id", ""), "")
        if not primary_user_id:
            continue
        # A5: simplex normalization — sample raw pcts then normalize to sum=1.0
        raw_pcts = [random.uniform(pct_min, pct_max) for (_, pct_min, pct_max) in credit_split_def]
        total_raw = sum(raw_pcts) or 1.0
        normalized_pcts = [round(p / total_raw, 4) for p in raw_pcts]
        for (credit_type, _pct_min, _pct_max), pct in zip(credit_split_def, normalized_pcts):
            if pct <= 0.001:
                continue
            if credit_type == "primary_ae":
                user_id_for_credit = primary_user_id
            else:
                other_users = [u for u in all_user_ids_list if u != primary_user_id]
                user_id_for_credit = random.choice(other_users) if other_users else primary_user_id
            credit_amount = round(deal_amount * pct, 2)
            sales_credit_rows.append({
                "id": str(uuid.uuid4()),
                "sales_unit_id": su_id,
                "user_id": user_id_for_credit,
                "credit_type": credit_type,
                "credit_percent": str(pct),
                "credit_amount": str(credit_amount),
                "source_system": "generated",
                "created_at": datetime.now(UTC).isoformat(),
            })

    # Attainment snapshots — monthly and quarterly per rep
    attainment_snapshot_rows: list[dict] = []
    quota_rows_local = dataset.get("quotas", [])
    # Aggregate revenue by (rep_id, period)
    rev_by_rep_period: dict[tuple[str, str], float] = {}
    for rv in revenue_rows:
        key = (rv.get("rep_id", ""), rv.get("period", ""))
        rev_by_rep_period[key] = rev_by_rep_period.get(key, 0.0) + float(rv.get("amount", 0) or 0)
    quota_by_rep_quarter: dict[tuple[str, str], float] = {}
    for q in quota_rows_local:
        quota_by_rep_quarter[(q.get("rep_id", ""), q.get("period", ""))] = float(q.get("amount", 0) or 0)
    seen_snap_monthly: set[tuple[str, str]] = set()
    for (rep_id_s, period_s), rev_amt_s in rev_by_rep_period.items():
        if (rep_id_s, period_s) in seen_snap_monthly:
            continue
        seen_snap_monthly.add((rep_id_s, period_s))
        try:
            m_date = date.fromisoformat(period_s + "-01")
            q_label = f"{m_date.year}-Q{(m_date.month - 1) // 3 + 1}"
        except ValueError:
            continue
        quarterly_quota = quota_by_rep_quarter.get((rep_id_s, q_label), 0.0)
        monthly_quota = quarterly_quota / 3.0 if quarterly_quota > 0 else 0.0
        attainment_pct_s = round((rev_amt_s / monthly_quota * 100) if monthly_quota > 0 else 0.0, 4)
        attainment_snapshot_rows.append({
            "id": str(uuid.uuid4()),
            "rep_id": rep_id_s,
            "period": period_s,
            "grain": "monthly",
            "revenue": str(round(rev_amt_s, 2)),
            "quota": str(round(monthly_quota, 2)),
            "attainment_pct": str(attainment_pct_s),
            "snapshot_date": datetime.now(UTC).date().isoformat(),
        })
    seen_snap_quarters: set[tuple[str, str]] = set()
    for (rep_id_s, q_label_s), q_amt_s in quota_by_rep_quarter.items():
        if (rep_id_s, q_label_s) in seen_snap_quarters:
            continue
        seen_snap_quarters.add((rep_id_s, q_label_s))
        try:
            yr_s, qn_s = q_label_s.split("-Q")
            q_months_s = [(int(qn_s) - 1) * 3 + m for m in range(1, 4)]
            q_rev_s = sum(rev_by_rep_period.get((rep_id_s, f"{yr_s}-{m:02d}"), 0.0) for m in q_months_s)
        except (ValueError, AttributeError):
            q_rev_s = 0.0
        attainment_pct_s = round((q_rev_s / q_amt_s * 100) if q_amt_s > 0 else 0.0, 4)
        attainment_snapshot_rows.append({
            "id": str(uuid.uuid4()),
            "rep_id": rep_id_s,
            "period": q_label_s,
            "grain": "quarterly",
            "revenue": str(round(q_rev_s, 2)),
            "quota": str(round(q_amt_s, 2)),
            "attainment_pct": str(attainment_pct_s),
            "snapshot_date": datetime.now(UTC).date().isoformat(),
        })

    return {
        "products": product_rows,
        "territories": territory_rows,
        "rep_hierarchy": hierarchy_rows,
        "positions": positions_rows,
        "users": users_rows,
        "managers": managers_rows,
        "plans": plan_rows,
        "rules": rule_rows,
        "plan_assignments": plan_assignments_rows,
        "rep_product_assignments": rep_product_assignments_rows,
        "user_territory_assignments": user_territory_assignments_rows,
        "rep_ramp": rep_ramp_rows,
        "bookings": booking_rows,
        "churn_events": churn_event_rows,
        "arr_waterfall": arr_waterfall_rows,
        "sales_units": sales_unit_rows,
        "sales_credits": sales_credit_rows,
        "attainment_snapshots": attainment_snapshot_rows,
        "leads": _build_leads(dataset, users_rows),
        "opportunities": _build_opportunities(dataset, users_rows),
    }


def _build_leads(dataset: dict[str, list[dict]], users_rows: list[dict]) -> list[dict]:
    """B3: Generate lead rows (~50% of accounts get 1-3 inbound/outbound leads)."""
    account_rows = dataset.get("accounts", [])
    lead_statuses = ["new", "qualified", "contacted", "disqualified", "converted"]
    lead_sources = ["inbound_web", "outbound_sdx", "referral", "event", "partner", "paid_ads"]
    lead_rows: list[dict] = []
    for account in account_rows:
        if random.random() < 0.50:
            continue  # ~50% of accounts don't have tracked leads
        n_leads = random.randint(1, 3)
        owner = random.choice(users_rows) if users_rows else None
        for _ in range(n_leads):
            status = random.choices(lead_statuses, weights=[20, 25, 20, 15, 20])[0]
            lead_rows.append({
                "id": str(uuid.uuid4()),
                "external_id": f"LEAD-{uuid.uuid4().hex[:8].upper()}",
                "account_id": account["id"],
                "owner_user_id": owner["id"] if owner else "",
                "source": random.choice(lead_sources),
                "status": status,
                "score": str(round(random.uniform(20.0, 95.0), 1)),
                "created_at": datetime.now(UTC).isoformat(),
                "source_system": "generated",
            })
    return lead_rows


def _build_opportunities(dataset: dict[str, list[dict]], users_rows: list[dict]) -> list[dict]:
    """B3: Generate opportunity rows — one per deal, using same account/owner mapping."""
    deal_rows = dataset.get("deals", [])
    rep_rows = dataset.get("reps", [])
    rep_to_user = {rep["id"]: user["id"] for rep, user in zip(rep_rows, users_rows)} if users_rows else {}
    opp_rows: list[dict] = []
    for deal in deal_rows:
        opp_rows.append({
            "id": str(uuid.uuid4()),
            "external_id": f"OPP-{deal['id'][:8].upper()}",
            "account_id": deal.get("account_id", ""),
            "owner_user_id": rep_to_user.get(deal.get("rep_id", ""), ""),
            "name": deal.get("name", ""),
            "stage": deal.get("stage", "Prospecting"),
            "amount": deal.get("amount", "0"),
            "close_date": deal.get("expected_close_date", ""),
            "source_system": "generated",
            "created_at": deal.get("created_at", datetime.now(UTC).isoformat()),
        })
    return opp_rows


def _write_extension_tables(company_dir: Path, extension_tables: dict[str, list[dict]]) -> list[str]:
    written: list[str] = []
    for table_name, rows in extension_tables.items():
        csv_path = company_dir / f"{table_name}.csv"
        _write_rows_csv(csv_path, rows)
        written.append(csv_path.name)
    return written


def random_date(start: date, end: date) -> date:
    if end < start:
        end = start
    return start + timedelta(days=random.randint(0, (end - start).days))


def _month_start_n_months_ago(anchor: date, months_ago: int) -> date:
    """Return first day of month exactly N months before anchor month."""
    year = anchor.year
    month = anchor.month - months_ago
    while month <= 0:
        month += 12
        year -= 1
    return date(year, month, 1)


def _safe_company_dir_name(company_name: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", company_name.strip()).strip("-").lower()
    return slug or "default-company"


def _add_months(d: date, delta_months: int) -> date:
    month0 = d.month - 1 + delta_months
    year = d.year + month0 // 12
    month = month0 % 12 + 1
    return date(year, month, 1)


def _quarter_periods(anchor: date, count: int = 6) -> list[str]:
    periods: list[str] = []
    y, q = anchor.year, (anchor.month - 1) // 3 + 1
    for _ in range(count):
        periods.append(f"{y}-Q{q}")
        q -= 1
        if q == 0:
            q, y = 4, y - 1
    return periods


def _quarter_start(period: str) -> date:
    yq, qq = period.split("-Q")
    period_month = (int(qq) - 1) * 3 + 1
    return date(int(yq), period_month, 1)


def _assign_rep_role_map(rep_ids: list[str], archetype: str) -> dict[str, str]:
    """Return {rep_id: role_name} based on archetype and headcount — mirrors hierarchy logic."""
    n = len(rep_ids)
    manager_span_cap = {"saas_smb": 8, "field_sales": 6, "saas_enterprise": 5, "overlay_specialist": 4}.get(archetype, 6)
    ic_role_mix = {
        "saas_smb": ["Account Executive", "Account Executive", "Sales Development Representative",
                     "Sales Development Representative", "Senior Account Executive"],
        "field_sales": ["Senior Account Executive", "Account Executive", "Account Executive",
                        "Account Executive", "Sales Development Representative"],
        "saas_enterprise": ["Senior Account Executive", "Senior Account Executive", "Account Executive",
                            "Account Executive", "Sales Development Representative"],
        "overlay_specialist": ["Overlay Specialist", "Overlay Specialist", "Senior Account Executive",
                               "Account Executive", "Sales Development Representative"],
    }.get(archetype, ["Account Executive", "Senior Account Executive", "Sales Development Representative"])

    cro_count = min(1, n)
    svp_count = 1 if n >= 30 else 0
    vp_count = 1 if n >= 25 else 0
    director_count = 1 if n >= 20 else 0
    base_leadership = cro_count + svp_count + vp_count + director_count
    remaining = max(0, n - base_leadership)
    manager_count = 0
    if remaining > 0:
        manager_count = max(1, (remaining + manager_span_cap - 1) // manager_span_cap)
        manager_count = min(manager_count, max(1, n // 3))
    while base_leadership + manager_count > n:
        manager_count -= 1

    role_map: dict[str, str] = {}
    idx = 0
    for rid in rep_ids[idx: idx + cro_count]:
        role_map[rid] = "Chief Revenue Officer"
    idx += cro_count
    for rid in rep_ids[idx: idx + svp_count]:
        role_map[rid] = "SVP Sales"
    idx += svp_count
    for rid in rep_ids[idx: idx + vp_count]:
        role_map[rid] = "VP Sales"
    idx += vp_count
    for rid in rep_ids[idx: idx + director_count]:
        role_map[rid] = "Director of Sales"
    idx += director_count
    for rid in rep_ids[idx: idx + manager_count]:
        role_map[rid] = "Sales Manager"
    idx += manager_count
    for i, rid in enumerate(rep_ids[idx:]):
        role_map[rid] = ic_role_mix[i % len(ic_role_mix)]
    return role_map


def _seasonal_random_date(start: date, end: date) -> date:
    """Sample a random date weighted by MONTH_MULTIPLIERS (A9 — seasonal deal creation)."""
    months_in_range: list[date] = []
    weights_in_range: list[float] = []
    cur = start.replace(day=1)
    while cur <= end:
        months_in_range.append(cur)
        weights_in_range.append(MONTH_MULTIPLIERS.get(cur.month, 1.0))
        m = cur.month + 1
        y = cur.year
        if m > 12:
            m, y = 1, y + 1
        cur = date(y, m, 1)
    if not months_in_range:
        return random_date(start, end)
    chosen_month = random.choices(months_in_range, weights=weights_in_range)[0]
    if chosen_month.month == 12:
        last_day = date(chosen_month.year + 1, 1, 1) - timedelta(days=1)
    else:
        last_day = date(chosen_month.year, chosen_month.month + 1, 1) - timedelta(days=1)
    day_start = max(start, chosen_month)
    day_end = min(end, last_day)
    if day_start > day_end:
        return random_date(start, end)
    return random_date(day_start, day_end)


def _build_revenue_rows_from_deals(
    reps: list[dict],
    deal_rows: list[dict],
    months: int,
    anchor: date,
    profile: dict,
) -> list[dict]:
    """
    A1: Build revenue rows purely from Closed Won deals — no random baseline.
    Each deal generates N monthly recognition rows based on contract term.
    A10: Rows are attenuated by hire-date ramp factor for the recognizing rep.
    """
    period_dates = [_month_start_n_months_ago(anchor, m) for m in range(months)]
    valid_periods = {p.strftime("%Y-%m") for p in period_dates}

    # Build hire_date lookup for ramp (A10)
    rep_hire_date: dict[str, date] = {}
    for rep in reps:
        hd = rep.get("hire_date")
        if isinstance(hd, date):
            rep_hire_date[str(rep["id"])] = hd
        elif isinstance(hd, str) and hd:
            try:
                rep_hire_date[str(rep["id"])] = date.fromisoformat(hd)
            except ValueError:
                pass

    primary_types = profile.get("primary_revenue_types", ["new_logo", "expansion", "renewal"])
    churn_rate = float(profile.get("churn_rate", 0.05))
    expansion_rate = float(profile.get("expansion_rate", 0.15))

    revenue_rows: list[dict] = []

    for deal in deal_rows:
        if deal.get("stage") != "Closed Won":
            continue
        rep_id = str(deal["rep_id"])
        amount = float(deal.get("amount", 0) or 0)
        if amount <= 0:
            continue
        if amount >= 200_000:
            term = 36
        elif amount >= 80_000:
            term = 24
        else:
            term = 12

        close_raw = deal.get("actual_close_date") or deal.get("expected_close_date") or anchor.isoformat()
        start_month = date.fromisoformat(close_raw[:10]).replace(day=1)
        rev_type = random.choice(primary_types)
        monthly_amt = amount / term
        hire_date = rep_hire_date.get(rep_id)

        for k in range(term):
            pdate = _add_months(start_month, k)
            period = pdate.strftime("%Y-%m")
            if period not in valid_periods:
                continue

            # A10: attenuate by ramp factor if rep was recently hired
            ramp = _ramp_factor(hire_date, pdate) if hire_date else 1.0
            if ramp <= 0:
                continue

            revenue_rows.append({
                "rep_id": rep_id,
                "period": period,
                "amount": str(round(monthly_amt * ramp, 2)),
                "account_id": str(deal.get("account_id", "")),
                "deal_id": str(deal["id"]),
                "revenue_type": rev_type,
                "contract_term_months": str(term),
                "recognition_start_date": pdate.isoformat(),
            })

    # Add churn and expansion rows derived from renewal base per (rep, period)
    renewal_base: dict[tuple[str, str], float] = {}
    for row in revenue_rows:
        if row.get("revenue_type") in ("new_logo", "renewal"):
            key = (row["rep_id"], row["period"])
            renewal_base[key] = renewal_base.get(key, 0.0) + float(row["amount"])

    for (rep_id, period), base_amt in renewal_base.items():
        if base_amt <= 0:
            continue
        pdate = date.fromisoformat(period + "-01")
        hire_date = rep_hire_date.get(rep_id)
        ramp = _ramp_factor(hire_date, pdate) if hire_date else 1.0
        if churn_rate > 0 and random.random() < churn_rate:
            churn_amt = round(base_amt * random.uniform(0.05, 0.20) * ramp, 2)
            revenue_rows.append({
                "rep_id": rep_id,
                "period": period,
                "amount": str(-churn_amt),
                "account_id": "",
                "deal_id": "",
                "revenue_type": "churn",
                "contract_term_months": "",
                "recognition_start_date": period + "-01",
            })
        if expansion_rate > 0 and random.random() < expansion_rate:
            exp_amt = round(base_amt * random.uniform(0.02, 0.15) * ramp, 2)
            revenue_rows.append({
                "rep_id": rep_id,
                "period": period,
                "amount": str(exp_amt),
                "account_id": "",
                "deal_id": "",
                "revenue_type": "expansion",
                "contract_term_months": "",
                "recognition_start_date": period + "-01",
            })

    return revenue_rows


#: Minimum quarterly quota. Ramp factors applied to a small base produced quotas
#: as low as $1.00, which turned attainment into a meaningless ratio — one rep
#: showed 768%.
MIN_QUARTERLY_QUOTA = 1_000.0

#: Target attainment bands and the share of rep-quarters that should land in
#: each. Chosen to exercise every tier of the generated comp plans (0-80, 80-100,
#: 100-120, 120+) rather than parking almost everyone in the top one, and to look
#: like a real sales organisation: most reps near plan, a minority short, a
#: minority accelerating.
ATTAINMENT_MIX: tuple[tuple[float, float, float], ...] = (
    (0.55, 0.80, 0.15),
    (0.80, 1.00, 0.25),
    (1.00, 1.20, 0.35),
    (1.20, 1.60, 0.25),
)


def _draw_target_attainment() -> float:
    """Pick a target attainment from ATTAINMENT_MIX."""
    roll = random.random()
    cumulative = 0.0
    for low, high, weight in ATTAINMENT_MIX:
        cumulative += weight
        if roll <= cumulative:
            return random.uniform(low, high)
    low, high, _ = ATTAINMENT_MIX[-1]
    return random.uniform(low, high)


def _calibrate_quotas_to_revenue(
    quota_rows: list[dict],
    revenue_rows: list[dict],
) -> list[dict]:
    """
    Rescale quotas against the revenue this generator actually produced.

    Quotas were built top-down from a constant annual figure and never referred
    to revenue, so the two drifted apart badly: average attainment of 218%, with
    individual rep-quarters at 452% and 768%. Beyond realism that defeats the
    compensation demo — the plans define four attainment tiers, and if nearly
    every rep lands in the top one the tiering is never exercised.

    Each rep-quarter's quota becomes `revenue / target`, with the target drawn
    from ATTAINMENT_MIX, so attainment lands where it was designed to. A
    rep-quarter with no revenue keeps its top-down quota — there is nothing to
    calibrate against — but is still floored.
    """
    revenue_by_rep_quarter: dict[tuple[str, str], float] = defaultdict(float)
    for row in revenue_rows:
        period = str(row.get("period", ""))
        if len(period) < 7:
            continue
        try:
            month = int(period[5:7])
        except ValueError:
            continue
        quarter = f"{period[:4]}-Q{(month - 1) // 3 + 1}"
        revenue_by_rep_quarter[(str(row.get("rep_id", "")), quarter)] += float(
            row.get("amount", 0) or 0
        )

    calibrated: list[dict] = []
    for row in quota_rows:
        key = (str(row.get("rep_id", "")), str(row.get("period", "")))
        revenue = revenue_by_rep_quarter.get(key, 0.0)
        if revenue > 0:
            amount = revenue / _draw_target_attainment()
        else:
            amount = float(row.get("amount", 0) or 0)
        calibrated.append({**row, "amount": str(round(max(amount, MIN_QUARTERLY_QUOTA), 2))})
    return calibrated


def _build_quota_rows_top_down(
    reps: list[dict],
    rep_role_map: dict[str, str],
    growth_factor: float,
    quarters: list[str],
    profile: dict | None = None,
) -> list[dict]:
    """A2: Top-down quota — base_annual_quota_ic × rank_multiplier × ramp × noise(±8%)."""
    base_ic_quota = (profile or {}).get("base_annual_quota_ic", BASE_ANNUAL_QUOTA)
    quota_rows: list[dict] = []
    for rep in reps:
        rep_id = str(rep["id"])
        role = rep_role_map.get(rep_id, "Account Executive")
        multiplier = QUOTA_RANK_MULTIPLIERS.get(role, 1.0)
        if multiplier == 0.0:
            continue  # CRO carries no individual quota
        hire_date = rep.get("hire_date")
        if isinstance(hire_date, str):
            try:
                hire_date = date.fromisoformat(hire_date)
            except ValueError:
                hire_date = None
        for period in quarters:
            period_date = _quarter_start(period)
            rf = _ramp_factor(hire_date, period_date) if hire_date else 1.0
            noise = random.uniform(0.92, 1.08)
            annual_target = base_ic_quota * multiplier * growth_factor * noise
            quarterly_quota = round(annual_target / 4.0 * rf, 2)
            quota_rows.append({"rep_id": rep_id, "period": period, "amount": str(quarterly_quota)})
    return quota_rows





def _generate_dataset(
    n_reps: int = 12,
    n_accounts: int = 60,
    n_deals: int = 150,
    months: int = 36,
    n_products: int = 5,
    target_total_revenue: float | None = None,
    archetype: str = "saas_enterprise",
    company_name: str = "default-company",
) -> dict[str, list[dict]]:
    """Generate complete synthetic dataset in memory."""
    profile = ARCHETYPE_PROFILES.get(archetype, ARCHETYPE_PROFILES["saas_enterprise"])
    dataset: dict[str, list[dict]] = {name: [] for name in TABLE_ORDER}

    email_domain = _company_email_domain(company_name)
    seen_emails: set[str] = set()

    teams = []
    for region in REGIONS:
        team_id = uuid.uuid4()
        teams.append({"id": team_id, "name": f"{region} Sales Team", "region": region})
        dataset["teams"].append({"id": str(team_id), "name": f"{region} Sales Team", "region": region})

    reps = []
    for _ in range(n_reps):
        rep_id = uuid.uuid4()
        full_name = fake.name()
        # A3: draw per-rep win_rate_bias from Beta distribution
        is_top_tier = random.random() < 0.30
        win_rate_bias = (
            min(1.0, max(0.0, random.betavariate(5, 3))) if is_top_tier
            else min(1.0, max(0.0, random.betavariate(3, 5)))
        )
        rep = {
            "id": rep_id,
            "team_id": random.choice(teams)["id"],
            "name": full_name,
            "email": _make_rep_email(full_name, email_domain, seen_emails),
            "region": random.choice(REGIONS),
            "hire_date": random_date(date(2019, 1, 1), date(2024, 6, 1)),
            "win_rate_bias": win_rate_bias,
        }
        reps.append(rep)
        dataset["reps"].append(
            {
                "id": str(rep["id"]),
                "team_id": str(rep["team_id"]),
                "name": rep["name"],
                "email": rep["email"],
                "region": rep["region"],
                "hire_date": rep["hire_date"].isoformat(),
            }
        )

    today = date.today()
    # Derive quota quarter count from history length: at least 6, covering full months window
    quota_quarter_count = max(6, (months + 2) // 3)
    quarters = _quarter_periods(today, count=quota_quarter_count)

    # A2 + A7: pre-assign roles to reps for quota and deal-weight stratification
    rep_role_map = _assign_rep_role_map([str(r["id"]) for r in reps], archetype)
    rep_deal_weights = [
        ROLE_DEAL_WEIGHTS.get(rep_role_map.get(str(r["id"]), "Account Executive"), 7.0)
        for r in reps
    ]

    growth_factor = profile["quota_growth_factor"]

    accounts = []
    for _ in range(n_accounts):
        account_id = uuid.uuid4()
        account = {
            "id": account_id,
            "name": fake.company(),
            "industry": random.choice(INDUSTRIES),
            "employee_count": random.choice([50, 100, 250, 500, 1000, 5000, 10000]),
            "annual_revenue": round(random.uniform(1e6, 5e8), 2),
        }
        accounts.append(account)
        dataset["accounts"].append(
            {
                "id": str(account_id),
                "name": account["name"],
                "industry": account["industry"],
                "employee_count": str(account["employee_count"]),
                "annual_revenue": str(account["annual_revenue"]),
            }
        )

    deals = []
    deal_size_min, deal_size_max = profile["deal_size_range"]
    stage_weights = profile["win_rate_weight"]
    deal_window_start = today - timedelta(days=months * 30)  # A6: match revenue window
    # Use archetype-appropriate product catalog for deal.product column
    deal_product_catalog = INSURANCE_PRODUCTS if archetype == "insurance" else PRODUCTS
    for _ in range(n_deals):
        # A7: role-stratified rep selection — ICs own most of the pipeline
        rep = random.choices(reps, weights=rep_deal_weights)[0]
        # A3: per-rep win rate bias adjusts Closed Won / Closed Lost stage weights
        win_bias = rep.get("win_rate_bias", 0.5)
        adjusted_weights = list(stage_weights[:4]) + [
            max(1, stage_weights[4] * (0.5 + win_bias)),   # Closed Won
            max(1, stage_weights[5] * (1.5 - win_bias)),   # Closed Lost
        ]
        stage = random.choices(STAGES, weights=adjusted_weights)[0]
        amount = round(random.uniform(deal_size_min, deal_size_max), 2)
        # A9: seasonal deal creation date sampling
        created = _seasonal_random_date(deal_window_start, today)
        close_noise = random.randint(-10, 10)
        prob = max(0, min(100, STAGE_PROB[stage] + close_noise))
        created_at = datetime.combine(created, datetime.min.time()) + timedelta(
            hours=random.randint(0, 23),
            minutes=random.randint(0, 59),
            seconds=random.randint(0, 59),
        )
        cycle_days = random.randint(*profile["cycle_days_range"])
        actual_close_date = (created + timedelta(days=cycle_days)) if stage in ("Closed Won", "Closed Lost") else None

        deal_id = uuid.uuid4()
        product_name = deal_product_catalog[_ % len(deal_product_catalog)] if n_products <= len(deal_product_catalog) else (
            deal_product_catalog[_ % len(deal_product_catalog)] if _ < len(deal_product_catalog) else f"{deal_product_catalog[(_ % len(deal_product_catalog))]}"
        )
        deal = {
            "id": deal_id,
            "account_id": random.choice(accounts)["id"],
            "rep_id": rep["id"],
            "name": f"{fake.bs().title()} Deal",
            "product": product_name,
            "stage": stage,
            "amount": amount,
            "close_probability": prob,
            "expected_close_date": created + timedelta(days=cycle_days + random.randint(0, 30)),
            "actual_close_date": actual_close_date,
            "created_at": created_at,
        }
        deals.append(deal)
        dataset["deals"].append(
            {
                "id": str(deal_id),
                "account_id": str(deal["account_id"]),
                "rep_id": str(deal["rep_id"]),
                "name": deal["name"],
                "product": deal["product"],
                "stage": deal["stage"],
                "amount": str(deal["amount"]),
                "close_probability": str(deal["close_probability"]),
                "expected_close_date": deal["expected_close_date"].isoformat(),
                "actual_close_date": deal["actual_close_date"].isoformat() if deal["actual_close_date"] else "",
                "created_at": deal["created_at"].isoformat(),
            }
        )

    for deal in deals:
        # A4: activity outcomes AND counts correlated with deal stage
        if deal["stage"] == "Closed Won":
            outcome_weights = [50, 30, 15, 5]   # positive, neutral, no_response, negative
            activity_count = random.randint(4, 10)  # won deals have more engagement
        elif deal["stage"] == "Closed Lost":
            outcome_weights = [15, 30, 25, 30]
            activity_count = random.randint(1, 4)   # lost deals have fewer touches
        else:
            outcome_weights = [25, 35, 25, 15]
            activity_count = random.randint(2, 7)   # open deals moderate
        for _ in range(activity_count):
            dataset["activities"].append(
                {
                    "id": str(uuid.uuid4()),
                    "deal_id": str(deal["id"]),
                    "rep_id": str(deal["rep_id"]),
                    "type": random.choice(ACT_TYPES),
                    "outcome": random.choices(ACT_OUTCOMES, weights=outcome_weights)[0],
                    "notes": fake.sentence(nb_words=12),
                    "activity_date": fake.date_time_between(start_date=deal["created_at"]).isoformat(),
                }
            )

    # Guarantee: every rep with at least one account gets a minimum of 1
    # Closed Won deal so that revenue rows exist and payout is non-zero.
    # Without this guarantee, statistically some reps end up with all-open
    # or all-lost pipelines which produces zero payout and misleading audits.
    rep_has_won: set = {d["rep_id"] for d in dataset["deals"] if d.get("stage") == "Closed Won"}
    account_ids = [a["id"] for a in dataset.get("accounts", [])]
    if account_ids:
        for rep in reps:
            if rep["id"] in rep_has_won:
                continue
            deal_size_min, deal_size_max = profile["deal_size_range"]
            amount = round(random.uniform(deal_size_min * 0.4, deal_size_max * 0.6), 2)
            cycle_days = random.randint(*profile["cycle_days_range"])
            created = _seasonal_random_date(
                today - timedelta(days=months * 30), today - timedelta(days=cycle_days + 10)
            )
            actual_close = created + timedelta(days=cycle_days)
            deal_id = uuid.uuid4()
            min_deal = {
                "id": str(deal_id),
                "account_id": str(random.choice(account_ids)),
                "rep_id": rep["id"],
                "name": f"{fake.bs().title()} Guaranteed Deal",
                "product": deal_product_catalog[0],
                "stage": "Closed Won",
                "amount": str(amount),
                "close_probability": "100",
                "expected_close_date": actual_close.isoformat(),
                "actual_close_date": actual_close.isoformat(),
                "created_at": datetime.combine(created, datetime.min.time()).isoformat(),
            }
            dataset["deals"].append(min_deal)
            rep_has_won.add(rep["id"])

    # Phase 2: Enforce minimum closed-won deal count per rep so attainment is
    # structurally achievable. Promote the largest open/proposal/negotiation deals
    # to Closed Won until the archetype threshold is met. If there are not enough
    # promotable deals, synthesize new closed-won deals sized to the rep's quota.
    min_deals_floor = profile.get("min_deals_won_per_rep", 3)
    base_ic_quota = profile.get("base_annual_quota_ic", BASE_ANNUAL_QUOTA)
    rep_won_counts: dict = {}
    for d in dataset["deals"]:
        if d.get("stage") == "Closed Won":
            rid = d["rep_id"]
            rep_won_counts[rid] = rep_won_counts.get(rid, 0) + 1

    promotable_stages = {"Proposal / Price Quote", "Negotiation / Review", "Value Proposition"}
    deal_size_min_p2, deal_size_max_p2 = profile["deal_size_range"]
    for rep in reps:
        rid = rep["id"]
        current = rep_won_counts.get(rid, 0)
        if current >= min_deals_floor:
            continue
        need = min_deals_floor - current
        # Promote existing pipeline deals first (largest first)
        candidates = [
            d for d in dataset["deals"]
            if d["rep_id"] == rid and d.get("stage") in promotable_stages
        ]
        candidates.sort(key=lambda d: float(d.get("amount", 0) or 0), reverse=True)
        promoted = 0
        for d in candidates[:need]:
            d["stage"] = "Closed Won"
            if not d.get("actual_close_date"):
                d["actual_close_date"] = d.get("expected_close_date") or today.isoformat()
            rep_won_counts[rid] = rep_won_counts.get(rid, 0) + 1
            promoted += 1
        # Synthesize new closed-won deals for any remaining shortfall
        still_need = need - promoted
        role_p2 = rep_role_map.get(str(rid), "Account Executive")
        mult_p2 = QUOTA_RANK_MULTIPLIERS.get(role_p2, 1.0)
        # Size synthetic deals to cover ~65% of the rep's total quota over the full period
        target_annual_rev = base_ic_quota * mult_p2 * profile.get("quota_growth_factor", 1.0)
        target_total_rev = target_annual_rev * (months / 12) * 0.65
        target_per_deal = max(deal_size_min_p2, target_total_rev / max(min_deals_floor, 1))
        target_per_deal = min(target_per_deal, deal_size_max_p2)
        for _ in range(still_need):
            amount = round(random.uniform(target_per_deal * 0.80, target_per_deal * 1.20), 2)
            cycle_days = random.randint(*profile["cycle_days_range"])
            # Bias synthetic floor deals to the most recent 40% of the history window
            # (up to 24 months back) so revenue recognition covers recent quarters.
            recent_start = today - timedelta(days=min(int(months * 30 * 0.40), 720))
            created = _seasonal_random_date(
                recent_start, today - timedelta(days=cycle_days + 10)
            )
            actual_close = created + timedelta(days=cycle_days)
            syn_deal = {
                "id": str(uuid.uuid4()),
                "account_id": str(random.choice(account_ids)) if account_ids else str(uuid.uuid4()),
                "rep_id": rid,
                "name": f"{fake.bs().title()} Floor Deal",
                "product": deal_product_catalog[0],
                "stage": "Closed Won",
                "amount": str(amount),
                "close_probability": "100",
                "expected_close_date": actual_close.isoformat(),
                "actual_close_date": actual_close.isoformat(),
                "created_at": datetime.combine(created, datetime.min.time()).isoformat(),
            }
            dataset["deals"].append(syn_deal)
            rep_won_counts[rid] = rep_won_counts.get(rid, 0) + 1

    dataset["revenue"] = _build_revenue_rows_from_deals(
        reps=reps,
        deal_rows=dataset["deals"],
        months=months,
        anchor=today,
        profile=profile,
    )

    _scale_revenue_to_target(dataset, target_total_revenue)

    dataset["quotas"] = _build_quota_rows_top_down(
        reps=reps,
        rep_role_map=rep_role_map,
        growth_factor=growth_factor,
        quarters=quarters,
        profile=profile,
    )
    # Revenue is built above, so quotas can be calibrated against what this
    # generator actually produced rather than against a constant.
    dataset["quotas"] = _calibrate_quotas_to_revenue(
        quota_rows=dataset["quotas"],
        revenue_rows=dataset.get("revenue", []),
    )

    _audit_generation_quality(dataset, profile, archetype)

    return dataset


def _audit_generation_quality(
    dataset: dict[str, list[dict]],
    profile: dict,
    archetype: str,
) -> None:
    """Log a concise attainment health-check so generation issues surface immediately."""
    quotas = dataset.get("quotas", [])
    revenue = dataset.get("revenue", [])

    quota_by_rep: dict[str, float] = {}
    for q in quotas:
        rid = q.get("rep_id", "")
        quota_by_rep[rid] = quota_by_rep.get(rid, 0.0) + float(q.get("amount", 0) or 0)

    revenue_by_rep: dict[str, float] = {}
    for r in revenue:
        rid = r.get("rep_id", "")
        revenue_by_rep[rid] = revenue_by_rep.get(rid, 0.0) + float(r.get("amount", 0) or 0)

    if not quota_by_rep:
        return

    attainments = []
    for rid, quota in quota_by_rep.items():
        rev = revenue_by_rep.get(rid, 0.0)
        if quota > 0:
            attainments.append(rev / quota * 100)

    if not attainments:
        return

    n = len(attainments)
    below_60 = sum(1 for a in attainments if a < 60)
    between_60_140 = sum(1 for a in attainments if 60 <= a < 140)
    above_140 = sum(1 for a in attainments if a >= 140)
    avg_att = sum(attainments) / n
    min_att = min(attainments)
    max_att = max(attainments)

    closed_won = [d for d in dataset.get("deals", []) if d.get("stage") == "Closed Won"]
    won_by_rep: dict[str, int] = {}
    for d in closed_won:
        rid = d["rep_id"]
        won_by_rep[rid] = won_by_rep.get(rid, 0) + 1
    avg_won = sum(won_by_rep.values()) / len(won_by_rep) if won_by_rep else 0

    import logging as _logging
    _log = _logging.getLogger(__name__)
    _log.info(
        "[AUDIT:%s] reps=%d  avg_att=%.1f%%  min=%.1f%%  max=%.1f%%  "
        "<60=%d  60-140=%d  >140=%d  avg_won_deals=%.1f",
        archetype, n, avg_att, min_att, max_att,
        below_60, between_60_140, above_140, avg_won,
    )
    if below_60 / n > 0.30:
        _log.warning(
            "[AUDIT:%s] %.0f%% of reps below 60%% attainment — "
            "consider raising base_annual_quota_ic or deal volume.",
            archetype, below_60 / n * 100,
        )


def _write_dataset_to_csv(dataset: dict[str, list[dict]], company_name: str, base_dir: str = "companies") -> Path:
    """Write complete dataset into companies/<company_name>/*.csv."""
    company_dir = Path(base_dir) / _safe_company_dir_name(company_name)
    company_dir.mkdir(parents=True, exist_ok=True)

    for table_name in TABLE_ORDER:
        rows = dataset.get(table_name, [])
        csv_path = company_dir / f"{table_name}.csv"
        if not rows:
            csv_path.write_text("", encoding="utf-8")
            continue

        fieldnames = list(rows[0].keys())
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)

    return company_dir


def _read_csv_rows(csv_path: Path) -> list[dict[str, str]]:
    if not csv_path.exists() or csv_path.stat().st_size == 0:
        return []
    with open(csv_path, "r", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _collect_csv_ingestion_runs(company_dir: Path, extra_tables: list[str] | None = None) -> list[IngestionRun]:
    """Create ingestion run metadata entries from generated CSV files."""
    runs: list[IngestionRun] = []
    table_names = TABLE_ORDER + sorted(set(extra_tables or []))
    for table_name in table_names:
        csv_path = company_dir / f"{table_name}.csv"
        rows = _read_csv_rows(csv_path)
        columns = list(rows[0].keys()) if rows else []
        run = IngestionRun(
            source_file=str(csv_path.resolve()),
            row_count=len(rows),
            column_count=len(columns),
            columns=columns,
        )
        if columns:
            run.compute_schema_hash()
        else:
            run.add_warning("CSV has no rows; schema hash not computed")
        runs.append(run)
    return runs


def _build_revops_reconciliation_snapshot(
    dataset: dict[str, list[dict]],
    extension_tables: dict[str, list[dict]],
    rep_quota_revenue_outlier_threshold: float = 5.0,
) -> dict[str, float | int]:
    deals = dataset.get("deals", [])
    quotas = dataset.get("quotas", [])
    revenue = dataset.get("revenue", [])
    bookings = extension_tables.get("bookings", [])
    reps = dataset.get("reps", [])
    teams = dataset.get("teams", [])

    rep_to_team = {r.get("id", ""): r.get("team_id", "") for r in reps}
    team_name_by_id = {t.get("id", ""): t.get("name", "") for t in teams}

    closed_won_total = sum(float(d.get("amount", 0) or 0) for d in deals if d.get("stage") == "Closed Won")
    booking_total = sum(float(b.get("amount", 0) or 0) for b in bookings)
    revenue_total = sum(float(r.get("amount", 0) or 0) for r in revenue)
    quota_total = sum(float(q.get("amount", 0) or 0) for q in quotas)

    quota_to_revenue_ratio = round(quota_total / revenue_total, 4) if revenue_total > 0 else 0.0
    booking_to_closed_won_ratio = round(booking_total / closed_won_total, 4) if closed_won_total > 0 else 0.0

    quota_by_rep: dict[str, float] = {}
    revenue_by_rep: dict[str, float] = {}
    for q in quotas:
        rep_id = q.get("rep_id", "")
        quota_by_rep[rep_id] = quota_by_rep.get(rep_id, 0.0) + float(q.get("amount", 0) or 0)
    for r in revenue:
        rep_id = r.get("rep_id", "")
        revenue_by_rep[rep_id] = revenue_by_rep.get(rep_id, 0.0) + float(r.get("amount", 0) or 0)

    rep_ratio_threshold = max(0.1, float(rep_quota_revenue_outlier_threshold))
    rep_ratio_outliers = 0
    rep_ratio_outliers_gt5x = 0
    for rep_id, quota_amt in quota_by_rep.items():
        rev_amt = revenue_by_rep.get(rep_id, 0.0)
        if rev_amt <= 0:
            continue
        ratio = quota_amt / rev_amt
        if ratio > rep_ratio_threshold:
            rep_ratio_outliers += 1
        if ratio > 5.0:
            rep_ratio_outliers_gt5x += 1

    quota_by_team: dict[str, float] = {}
    revenue_by_team: dict[str, float] = {}
    for rep_id, quota_amt in quota_by_rep.items():
        team_id = rep_to_team.get(rep_id, "")
        quota_by_team[team_id] = quota_by_team.get(team_id, 0.0) + quota_amt
    for rep_id, rev_amt in revenue_by_rep.items():
        team_id = rep_to_team.get(rep_id, "")
        revenue_by_team[team_id] = revenue_by_team.get(team_id, 0.0) + rev_amt

    team_ratios: dict[str, float] = {}
    for team_id, quota_amt in quota_by_team.items():
        rev_amt = revenue_by_team.get(team_id, 0.0)
        if rev_amt > 0:
            team_name = team_name_by_id.get(team_id, team_id or "unassigned")
            team_ratios[team_name] = round(quota_amt / rev_amt, 4)

    avg_team_ratio = round(sum(team_ratios.values()) / len(team_ratios), 4) if team_ratios else 0.0
    max_team_ratio = round(max(team_ratios.values()), 4) if team_ratios else 0.0

    return {
        "closed_won_total": round(closed_won_total, 2),
        "bookings_total": round(booking_total, 2),
        "revenue_total": round(revenue_total, 2),
        "quota_total": round(quota_total, 2),
        "quota_to_revenue_ratio": quota_to_revenue_ratio,
        "booking_to_closed_won_ratio": booking_to_closed_won_ratio,
        "closed_won_count": sum(1 for d in deals if d.get("stage") == "Closed Won"),
        "bookings_count": len(bookings),
        "rep_ratio_outlier_threshold": round(rep_ratio_threshold, 4),
        "rep_ratio_outliers": rep_ratio_outliers,
        "rep_ratio_outliers_gt5x": rep_ratio_outliers_gt5x,
        "team_ratio_avg": avg_team_ratio,
        "team_ratio_max": max_team_ratio,
        "team_count_with_ratio": len(team_ratios),
    }


def _write_ingestion_audit(
    company_dir: Path,
    company_name: str,
    db_counts: dict[str, int],
    runs: list[IngestionRun],
    validation_summary: dict | None = None,
    reconciliation_snapshot: dict | None = None,
) -> Path:
    """Persist per-run ingestion audit JSON for traceability."""
    total_rows = sum(run.row_count for run in runs)
    total_errors = sum(len(run.errors) for run in runs)
    total_warnings = sum(len(run.warnings) for run in runs)

    payload = {
        "run_id": str(uuid.uuid4()),
        "company_name": company_name,
        "company_directory": str(company_dir.resolve()),
        "generated_at": datetime.now(UTC).isoformat(),
        "status": "success" if total_errors == 0 else "partial",
        "total_csv_rows": total_rows,
        "db_rows_loaded": db_counts,
        "error_count": total_errors,
        "warning_count": total_warnings,
        "revops_validation": validation_summary or {},
        "revops_reconciliation": reconciliation_snapshot or {},
        "sources": [run.summary() for run in runs],
    }

    audit_path = company_dir / "ingestion_run_summary.json"
    audit_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return audit_path


def _parse_optional_date(raw: str | None) -> date | None:
    if not raw:
        return None
    return date.fromisoformat(raw)


# ── Position rank inference ──────────────────────────────────────────────────
_RANK_KEYWORDS = [
    (1, ["chief revenue officer", "cro", "chief executive", "ceo"]),
    (2, ["svp", "vp ", "vice president", "evp"]),
    (3, ["director"]),
    (4, ["manager", "head of"]),
    (5, ["account executive", "sales development", "sdr", "overlay", "specialist", "representative", "associate"]),
]
_RANK_LABELS = {1: "Executive", 2: "VP", 3: "Director", 4: "Manager", 5: "IC"}


def _infer_position_rank(name: str) -> int:
    nl = name.lower()
    for rank, keywords in _RANK_KEYWORDS:
        if any(k in nl for k in keywords):
            return rank
    return 99


def _parse_datetime(raw: str) -> datetime:
    dt = datetime.fromisoformat(raw)
    if dt.tzinfo is not None:
        return dt.astimezone(UTC).replace(tzinfo=None)
    return dt


def _utc_now_naive() -> datetime:
    """Return current UTC timestamp as offset-naive datetime for TIMESTAMP WITHOUT TIME ZONE columns."""
    return datetime.now(UTC).replace(tzinfo=None)


def _parse_bool(raw: str | None, default: bool = False) -> bool:
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "y"}


def _validate_company_csv_integrity(company_dir: Path) -> list[str]:
    """Validate key foreign-key relationships before loading into DB."""
    errors: list[str] = []

    team_rows = _read_csv_rows(company_dir / "teams.csv")
    rep_rows = _read_csv_rows(company_dir / "reps.csv")
    account_rows = _read_csv_rows(company_dir / "accounts.csv")
    deal_rows = _read_csv_rows(company_dir / "deals.csv")
    activity_rows = _read_csv_rows(company_dir / "activities.csv")
    quota_rows = _read_csv_rows(company_dir / "quotas.csv")
    revenue_rows = _read_csv_rows(company_dir / "revenue.csv")
    position_rows = _read_csv_rows(company_dir / "positions.csv")
    user_rows = _read_csv_rows(company_dir / "users.csv")
    manager_rows = _read_csv_rows(company_dir / "managers.csv")
    plan_rows = _read_csv_rows(company_dir / "plans.csv")
    rule_rows = _read_csv_rows(company_dir / "rules.csv")
    territory_rows = _read_csv_rows(company_dir / "territories.csv")
    plan_assignment_rows = _read_csv_rows(company_dir / "plan_assignments.csv")
    user_territory_assignment_rows = _read_csv_rows(company_dir / "user_territory_assignments.csv")

    team_ids = {row.get("id", "") for row in team_rows}
    rep_ids = {row.get("id", "") for row in rep_rows}
    account_ids = {row.get("id", "") for row in account_rows}
    deal_ids = {row.get("id", "") for row in deal_rows}
    position_ids = {row.get("id", "") for row in position_rows}
    user_ids = {row.get("id", "") for row in user_rows}
    plan_ids = {row.get("id", "") for row in plan_rows}
    territory_ids = {row.get("id", "") for row in territory_rows}

    missing_rep_teams = sorted({row.get("team_id", "") for row in rep_rows if row.get("team_id") and row.get("team_id") not in team_ids})
    if missing_rep_teams:
        errors.append(f"reps.csv has unknown team_id values: {', '.join(missing_rep_teams[:5])}")

    missing_deal_accounts = sorted({row.get("account_id", "") for row in deal_rows if row.get("account_id") and row.get("account_id") not in account_ids})
    if missing_deal_accounts:
        errors.append(f"deals.csv has unknown account_id values: {', '.join(missing_deal_accounts[:5])}")

    missing_deal_reps = sorted({row.get("rep_id", "") for row in deal_rows if row.get("rep_id") and row.get("rep_id") not in rep_ids})
    if missing_deal_reps:
        errors.append(f"deals.csv has unknown rep_id values: {', '.join(missing_deal_reps[:5])}")

    missing_activity_deals = sorted({row.get("deal_id", "") for row in activity_rows if row.get("deal_id") and row.get("deal_id") not in deal_ids})
    if missing_activity_deals:
        errors.append(f"activities.csv has unknown deal_id values: {', '.join(missing_activity_deals[:5])}")

    missing_activity_reps = sorted({row.get("rep_id", "") for row in activity_rows if row.get("rep_id") and row.get("rep_id") not in rep_ids})
    if missing_activity_reps:
        errors.append(f"activities.csv has unknown rep_id values: {', '.join(missing_activity_reps[:5])}")

    missing_quota_reps = sorted({row.get("rep_id", "") for row in quota_rows if row.get("rep_id") and row.get("rep_id") not in rep_ids})
    if missing_quota_reps:
        errors.append(f"quotas.csv has unknown rep_id values: {', '.join(missing_quota_reps[:5])}")

    missing_revenue_reps = sorted({row.get("rep_id", "") for row in revenue_rows if row.get("rep_id") and row.get("rep_id") not in rep_ids})
    if missing_revenue_reps:
        errors.append(f"revenue.csv has unknown rep_id values: {', '.join(missing_revenue_reps[:5])}")

    missing_user_positions = sorted({row.get("position_id", "") for row in user_rows if row.get("position_id") and row.get("position_id") not in position_ids})
    if missing_user_positions:
        errors.append(f"users.csv has unknown position_id values: {', '.join(missing_user_positions[:5])}")

    missing_manager_users = sorted({row.get("user_id", "") for row in manager_rows if row.get("user_id") and row.get("user_id") not in user_ids})
    if missing_manager_users:
        errors.append(f"managers.csv has unknown user_id values: {', '.join(missing_manager_users[:5])}")

    missing_manager_refs = sorted({row.get("manager_user_id", "") for row in manager_rows if row.get("manager_user_id") and row.get("manager_user_id") not in user_ids})
    if missing_manager_refs:
        errors.append(f"managers.csv has unknown manager_user_id values: {', '.join(missing_manager_refs[:5])}")

    missing_rule_plans = sorted({row.get("plan_id", "") for row in rule_rows if row.get("plan_id") and row.get("plan_id") not in plan_ids})
    if missing_rule_plans:
        errors.append(f"rules.csv has unknown plan_id values: {', '.join(missing_rule_plans[:5])}")

    missing_pa_users = sorted({row.get("user_id", "") for row in plan_assignment_rows if row.get("user_id") and row.get("user_id") not in user_ids})
    if missing_pa_users:
        errors.append(f"plan_assignments.csv has unknown user_id values: {', '.join(missing_pa_users[:5])}")

    missing_pa_plans = sorted({row.get("plan_id", "") for row in plan_assignment_rows if row.get("plan_id") and row.get("plan_id") not in plan_ids})
    if missing_pa_plans:
        errors.append(f"plan_assignments.csv has unknown plan_id values: {', '.join(missing_pa_plans[:5])}")

    missing_uta_users = sorted({row.get("user_id", "") for row in user_territory_assignment_rows if row.get("user_id") and row.get("user_id") not in user_ids})
    if missing_uta_users:
        errors.append(f"user_territory_assignments.csv has unknown user_id values: {', '.join(missing_uta_users[:5])}")

    missing_uta_territories = sorted({row.get("territory_id", "") for row in user_territory_assignment_rows if row.get("territory_id") and row.get("territory_id") not in territory_ids})
    if missing_uta_territories:
        errors.append(f"user_territory_assignments.csv has unknown territory_id values: {', '.join(missing_uta_territories[:5])}")

    return errors


async def _delete_company_rows(company: str) -> int:
    """
    Remove every row belonging to `company`, leaving other tenants untouched.

    Deletes in reverse dependency order so foreign keys are satisfied without
    CASCADE. Runs unscoped on purpose: the statement already targets one company
    by an explicit predicate, and the read filter would otherwise stack on top.
    """
    from sqlalchemy import delete as _sa_delete

    from backend.tenant_guard import unscoped as _unscoped

    removed = 0
    session_factory = get_session_factory()
    async with session_factory() as db, _unscoped():
        for table in reversed(Base.metadata.sorted_tables):
            if "company_id" not in table.c:
                continue
            result = await db.execute(_sa_delete(table).where(table.c.company_id == company))
            removed += result.rowcount or 0
        await db.commit()
    return removed


async def _load_csvs_into_database(company_dir: Path, company_id: str | None = None) -> dict[str, int]:
    """
    Replace one company's rows with the contents of its CSV folder.

    This used to drop and recreate *every* table, which is what made tenancy a
    whole-database swap: loading one company destroyed all the others, so the
    server could hold exactly one at a time and a request naming a different
    company rebuilt the database to satisfy it.

    The scope of the operation now matches its meaning. Only this company's rows
    are removed, inserts are stamped with its id by the tenant guard, and other
    tenants are untouched — so companies can be resident together and no read can
    trigger a rebuild.
    """
    company = (company_id or company_dir.name).strip()
    if not company:
        raise ValueError("A company id is required to load a dataset.")

    # Creating absent tables is not tenant-scoped; dropping them was the bug.
    _engine = get_engine()
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    await _delete_company_rows(company)

    _session_factory = get_session_factory()
    async with _session_factory() as db, tenant_scope(company):
        counts: dict[str, int] = {}

        team_rows = _read_csv_rows(company_dir / "teams.csv")
        for row in team_rows:
            db.add(Team(id=uuid.UUID(row["id"]), name=row["name"], region=row.get("region")))
        counts["teams"] = len(team_rows)
        await db.flush()

        rep_rows = _read_csv_rows(company_dir / "reps.csv")
        for row in rep_rows:
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
        counts["reps"] = len(rep_rows)
        await db.flush()

        quota_rows = _read_csv_rows(company_dir / "quotas.csv")
        for row in quota_rows:
            db.add(Quota(rep_id=uuid.UUID(row["rep_id"]), period=row["period"], amount=float(row["amount"])))
        counts["quotas"] = len(quota_rows)
        await db.flush()

        account_rows = _read_csv_rows(company_dir / "accounts.csv")
        for row in account_rows:
            db.add(
                Account(
                    id=uuid.UUID(row["id"]),
                    name=row["name"],
                    industry=row.get("industry"),
                    employee_count=int(row["employee_count"]) if row.get("employee_count") else None,
                    annual_revenue=float(row["annual_revenue"]) if row.get("annual_revenue") else None,
                )
            )
        counts["accounts"] = len(account_rows)
        await db.flush()

        deal_rows = _read_csv_rows(company_dir / "deals.csv")
        for row in deal_rows:
            db.add(
                Deal(
                    id=uuid.UUID(row["id"]),
                    account_id=uuid.UUID(row["account_id"]),
                    rep_id=uuid.UUID(row["rep_id"]),
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
        counts["deals"] = len(deal_rows)
        await db.flush()

        activity_rows = _read_csv_rows(company_dir / "activities.csv")
        for row in activity_rows:
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
        counts["activities"] = len(activity_rows)
        await db.flush()

        revenue_rows = _read_csv_rows(company_dir / "revenue.csv")
        for row in revenue_rows:
            db.add(Revenue(
                rep_id=uuid.UUID(row["rep_id"]),
                period=row["period"],
                amount=float(row["amount"]),
                # SaaS/RevOps fields — populated when CSV provides them
                account_id=uuid.UUID(row["account_id"]) if row.get("account_id") else None,
                deal_id=uuid.UUID(row["deal_id"]) if row.get("deal_id") else None,
                revenue_type=row.get("revenue_type"),
                contract_term_months=int(row["contract_term_months"]) if row.get("contract_term_months") else None,
                recognition_start_date=_parse_optional_date(row.get("recognition_start_date")),
                product_sku=row.get("product_sku"),
                is_recurring=row.get("is_recurring", "").lower() == "true" if row.get("is_recurring") else None,
            ))
        counts["revenue"] = len(revenue_rows)

        position_rows = _read_csv_rows(company_dir / "positions.csv")
        for row in position_rows:
            db.add(
                Position(
                    id=uuid.UUID(row["id"]),
                    external_id=row.get("external_id"),
                    name=row["name"],
                    level=row.get("level"),
                    rank=int(row["rank"]) if row.get("rank") else _infer_position_rank(row["name"]),
                    rank_label=row.get("rank_label") or _RANK_LABELS.get(_infer_position_rank(row["name"])),
                    source_system=row.get("source_system"),
                    created_at=_parse_datetime(row["created_at"]) if row.get("created_at") else _utc_now_naive(),
                    effective_start_date=_parse_optional_date(row.get("effective_start_date")),
                    effective_end_date=_parse_optional_date(row.get("effective_end_date")),
                )
            )
        counts["positions"] = len(position_rows)
        await db.flush()

        user_rows = _read_csv_rows(company_dir / "users.csv")
        for row in user_rows:
            db.add(
                UserProfile(
                    id=uuid.UUID(row["id"]),
                    external_id=row.get("external_id"),
                    position_id=uuid.UUID(row["position_id"]) if row.get("position_id") else None,
                    team_id=uuid.UUID(row["team_id"]) if row.get("team_id") else None,
                    name=row["name"],
                    email=row["email"],
                    region=row.get("region"),
                    hire_date=_parse_optional_date(row.get("hire_date")),
                    source_system=row.get("source_system"),
                    mapping_basis=row.get("mapping_basis"),
                    evidence_score=float(row["evidence_score"]) if row.get("evidence_score") else None,
                    created_at=_parse_datetime(row["created_at"]) if row.get("created_at") else _utc_now_naive(),
                    effective_start_date=_parse_optional_date(row.get("effective_start_date")),
                    effective_end_date=_parse_optional_date(row.get("effective_end_date")),
                )
            )
        counts["users"] = len(user_rows)
        await db.flush()

        manager_rows = _read_csv_rows(company_dir / "managers.csv")
        for row in manager_rows:
            db.add(
                Manager(
                    id=uuid.UUID(row["id"]),
                    user_id=uuid.UUID(row["user_id"]),
                    manager_user_id=uuid.UUID(row["manager_user_id"]) if row.get("manager_user_id") else None,
                    source_system=row.get("source_system"),
                    created_at=_parse_datetime(row["created_at"]) if row.get("created_at") else _utc_now_naive(),
                )
            )
        counts["managers"] = len(manager_rows)
        await db.flush()

        # Legacy healthcare datasets may provide plan_name/rule_name without UUID IDs.
        # Normalize those rows into canonical Plan/Rule records instead of failing load.
        legacy_rule_to_plan_name: dict[str, str] = {}
        for row in rep_rows:
            rule_name = str(row.get("rule") or "").strip()
            plan_name = str(row.get("plan") or "").strip()
            if rule_name and plan_name and rule_name not in legacy_rule_to_plan_name:
                legacy_rule_to_plan_name[rule_name] = plan_name

        plan_rows = _read_csv_rows(company_dir / "plans.csv")
        plan_id_by_name: dict[str, uuid.UUID] = {}
        inserted_plan_ids: set[uuid.UUID] = set()
        plan_count = 0
        for row in plan_rows:
            raw_name = str(row.get("name") or row.get("plan_name") or "").strip()
            raw_id = str(row.get("id") or "").strip()
            if not raw_name and not raw_id:
                continue

            if raw_id:
                try:
                    plan_id = uuid.UUID(raw_id)
                except ValueError:
                    if not raw_name:
                        continue
                    plan_id = uuid.uuid5(uuid.NAMESPACE_URL, f"legacy-plan::{raw_name}")
            else:
                plan_id = uuid.uuid5(uuid.NAMESPACE_URL, f"legacy-plan::{raw_name}")

            if plan_id in inserted_plan_ids:
                if raw_name:
                    plan_id_by_name.setdefault(raw_name, plan_id)
                continue

            scope_raw = str(row.get("scope") or row.get("plan_type") or "individual").strip().lower()
            scope = scope_raw if scope_raw in {"global", "department", "team", "individual"} else "individual"
            owner_user_id = None
            if row.get("owner_user_id"):
                try:
                    owner_user_id = uuid.UUID(str(row.get("owner_user_id")))
                except ValueError:
                    owner_user_id = None

            name = raw_name or f"Plan {str(plan_id)[:8]}"
            db.add(
                Plan(
                    id=plan_id,
                    external_id=row.get("external_id"),
                    name=name,
                    description=row.get("description"),
                    scope=scope,
                    owner_user_id=owner_user_id,
                    source_system=row.get("source_system") or "uploaded",
                    created_at=_parse_datetime(row["created_at"]) if row.get("created_at") else _utc_now_naive(),
                    effective_start_date=_parse_optional_date(row.get("effective_start_date")),
                    effective_end_date=_parse_optional_date(row.get("effective_end_date")),
                )
            )
            inserted_plan_ids.add(plan_id)
            plan_id_by_name.setdefault(name, plan_id)
            plan_count += 1

        counts["plans"] = plan_count
        await db.flush()

        rule_rows = _read_csv_rows(company_dir / "rules.csv")
        inserted_rule_ids: set[uuid.UUID] = set()
        default_plan_id = next(iter(inserted_plan_ids), None)
        rule_count = 0
        for row in rule_rows:
            raw_name = str(row.get("name") or row.get("rule_name") or "").strip()
            raw_id = str(row.get("id") or "").strip()
            raw_plan_id = str(row.get("plan_id") or "").strip()
            plan_name_hint = str(row.get("plan_name") or "").strip()

            plan_id = None
            if raw_plan_id:
                try:
                    plan_id = uuid.UUID(raw_plan_id)
                except ValueError:
                    plan_id = None
            if plan_id is None and plan_name_hint:
                plan_id = plan_id_by_name.get(plan_name_hint)
            if plan_id is None and raw_name:
                legacy_plan_name = legacy_rule_to_plan_name.get(raw_name)
                if legacy_plan_name:
                    plan_id = plan_id_by_name.get(legacy_plan_name)
            if plan_id is None:
                plan_id = default_plan_id
            if plan_id is None:
                continue

            if raw_id:
                try:
                    rule_id = uuid.UUID(raw_id)
                except ValueError:
                    rule_id = uuid.uuid5(uuid.NAMESPACE_URL, f"legacy-rule::{raw_name}::{plan_id}")
            else:
                rule_id = uuid.uuid5(uuid.NAMESPACE_URL, f"legacy-rule::{raw_name}::{plan_id}")

            if rule_id in inserted_rule_ids:
                continue

            metric_name = row.get("metric_name") or row.get("rule_type") or "attainment_pct"
            db.add(
                Rule(
                    id=rule_id,
                    plan_id=plan_id,
                    name=raw_name or f"Rule {str(rule_id)[:8]}",
                    metric_name=metric_name,
                    threshold_min=float(row["threshold_min"]) if row.get("threshold_min") else 0.0,
                    threshold_max=float(row["threshold_max"]) if row.get("threshold_max") else 999.0,
                    rate=float(row["rate"]) if row.get("rate") else 0.03,
                    bonus_amount=float(row["bonus_amount"]) if row.get("bonus_amount") else 0.0,
                    source_system=row.get("source_system") or "uploaded",
                    created_at=_parse_datetime(row["created_at"]) if row.get("created_at") else _utc_now_naive(),
                )
            )
            inserted_rule_ids.add(rule_id)
            rule_count += 1

        counts["rules"] = rule_count
        await db.flush()

        territory_rows = _read_csv_rows(company_dir / "territories.csv")
        territory_count = 0
        for row in territory_rows:
            territory_name = str(row.get("name") or row.get("territory") or "").strip()
            raw_territory_id = str(row.get("id") or "").strip()
            if raw_territory_id:
                try:
                    territory_id = uuid.UUID(raw_territory_id)
                except ValueError:
                    if not territory_name:
                        continue
                    territory_id = uuid.uuid5(uuid.NAMESPACE_URL, f"legacy-territory::{territory_name}")
            else:
                if not territory_name:
                    continue
                territory_id = uuid.uuid5(uuid.NAMESPACE_URL, f"legacy-territory::{territory_name}")

            parent_territory_id = None
            raw_parent_id = str(row.get("parent_territory_id") or "").strip()
            if raw_parent_id:
                try:
                    parent_territory_id = uuid.UUID(raw_parent_id)
                except ValueError:
                    parent_territory_id = None

            db.add(
                Territory(
                    id=territory_id,
                    external_id=row.get("external_id"),
                    territory_code=row.get("territory_code"),
                    name=territory_name,
                    parent_territory_id=parent_territory_id,
                    region=row.get("region"),
                    segment=row.get("segment"),
                    source_system=row.get("source_system"),
                    created_at=_parse_datetime(row["created_at"]) if row.get("created_at") else _utc_now_naive(),
                    effective_start_date=_parse_optional_date(row.get("effective_start_date")),
                    effective_end_date=_parse_optional_date(row.get("effective_end_date")),
                )
            )
            territory_count += 1
        counts["territories"] = territory_count
        await db.flush()

        plan_assignment_rows = _read_csv_rows(company_dir / "plan_assignments.csv")
        plan_assignment_count = 0
        for row in plan_assignment_rows:
            raw_user_id = str(row.get("user_id") or "").strip()
            raw_plan_id = str(row.get("plan_id") or "").strip()
            if not raw_user_id or not raw_plan_id:
                continue
            try:
                user_id = uuid.UUID(raw_user_id)
                plan_id = uuid.UUID(raw_plan_id)
            except ValueError:
                continue

            raw_assignment_id = str(row.get("id") or "").strip()
            if raw_assignment_id:
                try:
                    assignment_id = uuid.UUID(raw_assignment_id)
                except ValueError:
                    assignment_id = uuid.uuid5(uuid.NAMESPACE_URL, f"legacy-plan-assignment::{user_id}::{plan_id}")
            else:
                assignment_id = uuid.uuid5(uuid.NAMESPACE_URL, f"legacy-plan-assignment::{user_id}::{plan_id}")

            db.add(
                PlanAssignment(
                    id=assignment_id,
                    user_id=user_id,
                    plan_id=plan_id,
                    effective_start_date=_parse_optional_date(row.get("effective_start_date")),
                    effective_end_date=_parse_optional_date(row.get("effective_end_date")),
                    source_system=row.get("source_system"),
                    mapping_basis=row.get("mapping_basis"),
                    evidence_score=float(row["evidence_score"]) if row.get("evidence_score") else None,
                    created_at=_parse_datetime(row["created_at"]) if row.get("created_at") else _utc_now_naive(),
                )
            )
            plan_assignment_count += 1
        counts["plan_assignments"] = plan_assignment_count
        await db.flush()

        # Products — loaded here so rep_product_assignments can FK to them
        product_rows = _read_csv_rows(company_dir / "products.csv")
        product_count = 0
        for row in product_rows:
            product_name = str(row.get("name") or row.get("product_name") or "").strip()
            raw_product_id = str(row.get("id") or "").strip()
            if raw_product_id:
                try:
                    product_id = uuid.UUID(raw_product_id)
                except ValueError:
                    if not product_name:
                        continue
                    product_id = uuid.uuid5(uuid.NAMESPACE_URL, f"legacy-product::{product_name}")
            else:
                if not product_name:
                    continue
                product_id = uuid.uuid5(uuid.NAMESPACE_URL, f"legacy-product::{product_name}")

            db.add(
                Product(
                    id=product_id,
                    external_id=row.get("external_id"),
                    product_sku=row.get("product_sku"),
                    name=product_name,
                    category=row.get("category") or row.get("product_family"),
                    source_system=row.get("source_system"),
                    created_at=_parse_datetime(row["created_at"]) if row.get("created_at") else _utc_now_naive(),
                )
            )
            product_count += 1
        counts["products"] = product_count
        await db.flush()

        # Rep product assignments
        rep_product_assignment_rows = _read_csv_rows(company_dir / "rep_product_assignments.csv")
        for row in rep_product_assignment_rows:
            db.add(
                RepProductAssignment(
                    id=uuid.UUID(row["id"]),
                    rep_id=uuid.UUID(row["rep_id"]),
                    product_id=uuid.UUID(row["product_id"]) if row.get("product_id") else None,
                    is_primary=_parse_bool(row.get("is_primary"), default=False),
                    specialization=row.get("specialization"),
                    effective_start_date=_parse_optional_date(row.get("effective_start_date")),
                    effective_end_date=_parse_optional_date(row.get("effective_end_date")),
                    source_system=row.get("source_system"),
                    created_at=_parse_datetime(row["created_at"]) if row.get("created_at") else _utc_now_naive(),
                )
            )
        counts["rep_product_assignments"] = len(rep_product_assignment_rows)
        await db.flush()

        user_territory_assignment_rows = _read_csv_rows(company_dir / "user_territory_assignments.csv")
        for row in user_territory_assignment_rows:
            db.add(
                UserTerritoryAssignment(
                    id=uuid.UUID(row["id"]),
                    user_id=uuid.UUID(row["user_id"]),
                    territory_id=uuid.UUID(row["territory_id"]),
                    is_primary=_parse_bool(row.get("is_primary"), default=False),
                    source_system=row.get("source_system"),
                    mapping_basis=row.get("mapping_basis"),
                    evidence_score=float(row["evidence_score"]) if row.get("evidence_score") else None,
                    created_at=_parse_datetime(row["created_at"]) if row.get("created_at") else _utc_now_naive(),
                    effective_start_date=_parse_optional_date(row.get("effective_start_date")),
                    effective_end_date=_parse_optional_date(row.get("effective_end_date")),
                )
            )
        counts["user_territory_assignments"] = len(user_territory_assignment_rows)

        # B1: AttainmentSnapshot — monthly and quarterly attainment per rep
        attainment_snapshot_rows = _read_csv_rows(company_dir / "attainment_snapshots.csv")
        seen_snap: set[tuple[str, str, str]] = set()
        for row in attainment_snapshot_rows:
            key = (row.get("rep_id", ""), row.get("period", ""), row.get("grain", ""))
            if key in seen_snap:
                continue
            seen_snap.add(key)
            db.add(AttainmentSnapshot(
                id=uuid.UUID(row["id"]) if row.get("id") else uuid.uuid4(),
                rep_id=uuid.UUID(row["rep_id"]) if row.get("rep_id") else None,
                period=row["period"],
                grain=row.get("grain"),
                revenue=float(row["revenue"]) if row.get("revenue") else None,
                quota=float(row["quota"]) if row.get("quota") else None,
                attainment_pct=float(row["attainment_pct"]) if row.get("attainment_pct") else None,
                snapshot_date=_parse_optional_date(row.get("snapshot_date")),
            ))
        counts["attainment_snapshots"] = len(attainment_snapshot_rows)
        await db.flush()

        # B1: RepRamp — ramp factor per rep per period
        rep_ramp_rows = _read_csv_rows(company_dir / "rep_ramp.csv")
        seen_ramp: set[tuple[str, str]] = set()
        for row in rep_ramp_rows:
            key = (row.get("rep_id", ""), row.get("period", ""))
            if key in seen_ramp:
                continue
            seen_ramp.add(key)
            db.add(RepRamp(
                rep_id=uuid.UUID(row["rep_id"]) if row.get("rep_id") else None,
                period=row["period"],
                months_since_hire=int(row["months_since_hire"]) if row.get("months_since_hire") else None,
                ramp_factor=float(row["ramp_factor"]) if row.get("ramp_factor") else None,
                quota_at_ramp=float(row["quota_at_ramp"]) if row.get("quota_at_ramp") else None,
                full_quota=float(row["full_quota"]) if row.get("full_quota") else None,
                is_ramping=_parse_bool(row.get("is_ramping"), default=False),
            ))
        counts["rep_ramp"] = len(rep_ramp_rows)

        # B3: Leads
        lead_rows_csv = _read_csv_rows(company_dir / "leads.csv")
        for row in lead_rows_csv:
            db.add(Lead(
                id=uuid.UUID(row["id"]) if row.get("id") else uuid.uuid4(),
                external_id=row.get("external_id"),
                account_id=uuid.UUID(row["account_id"]) if row.get("account_id") else None,
                owner_user_id=uuid.UUID(row["owner_user_id"]) if row.get("owner_user_id") else None,
                source=row.get("source"),
                status=row.get("status"),
                score=float(row["score"]) if row.get("score") else None,
                source_system=row.get("source_system"),
                created_at=_parse_datetime(row["created_at"]) if row.get("created_at") else _utc_now_naive(),
            ))
        counts["leads"] = len(lead_rows_csv)
        await db.flush()

        # B3: Opportunities
        opp_rows_csv = _read_csv_rows(company_dir / "opportunities.csv")
        for row in opp_rows_csv:
            db.add(Opportunity(
                id=uuid.UUID(row["id"]) if row.get("id") else uuid.uuid4(),
                external_id=row.get("external_id"),
                account_id=uuid.UUID(row["account_id"]) if row.get("account_id") else None,
                owner_user_id=uuid.UUID(row["owner_user_id"]) if row.get("owner_user_id") else None,
                name=row.get("name", ""),
                stage=row.get("stage"),
                amount=float(row["amount"]) if row.get("amount") else None,
                close_date=_parse_optional_date(row.get("close_date")),
                source_system=row.get("source_system"),
                created_at=_parse_datetime(row["created_at"]) if row.get("created_at") else _utc_now_naive(),
            ))
        counts["opportunities"] = len(opp_rows_csv)

        # B1/B2: ARR waterfall (rep-period ARR movement)
        arr_rows_csv = _read_csv_rows(company_dir / "arr_waterfall.csv")
        for row in arr_rows_csv:
            db.add(ArrWaterfallEntry(
                id=uuid.UUID(row["id"]) if row.get("id") else uuid.uuid4(),
                rep_id=uuid.UUID(row["rep_id"]) if row.get("rep_id") else None,
                period=row.get("period", ""),
                mrr_new=float(row["mrr_new"]) if row.get("mrr_new") else 0.0,
                mrr_expansion=float(row["mrr_expansion"]) if row.get("mrr_expansion") else 0.0,
                mrr_contraction=float(row["mrr_contraction"]) if row.get("mrr_contraction") else 0.0,
                mrr_churn=float(row["mrr_churn"]) if row.get("mrr_churn") else 0.0,
                mrr_renewal=float(row["mrr_renewal"]) if row.get("mrr_renewal") else 0.0,
                mrr_net=float(row["mrr_net"]) if row.get("mrr_net") else 0.0,
                arr_start=float(row["arr_start"]) if row.get("arr_start") else 0.0,
                arr_end=float(row["arr_end"]) if row.get("arr_end") else 0.0,
            ))
        counts["arr_waterfall"] = len(arr_rows_csv)
        await db.flush()

        # B1: Bookings
        booking_rows_csv = _read_csv_rows(company_dir / "bookings.csv")
        for row in booking_rows_csv:
            db.add(Booking(
                id=uuid.UUID(row.get("id") or row.get("booking_id") or str(uuid.uuid4())),
                deal_id=uuid.UUID(row["deal_id"]) if row.get("deal_id") else None,
                rep_id=uuid.UUID(row["rep_id"]) if row.get("rep_id") else None,
                account_id=uuid.UUID(row["account_id"]) if row.get("account_id") else None,
                booking_date=_parse_optional_date(row.get("booking_date")),
                amount=float(row["amount"]) if row.get("amount") else None,
                arr=float(row["arr"]) if row.get("arr") else None,
                mrr=float(row["mrr"]) if row.get("mrr") else None,
                product_sku=row.get("product_sku"),
                contract_term_months=int(row["contract_term_months"]) if row.get("contract_term_months") else None,
                revenue_type=row.get("revenue_type"),
                recognition_start_date=_parse_optional_date(row.get("recognition_start_date")),
                created_at=_parse_datetime(row["created_at"]) if row.get("created_at") else _utc_now_naive(),
            ))
        counts["bookings"] = len(booking_rows_csv)
        await db.flush()

        # B1: Churn events
        churn_rows_csv = _read_csv_rows(company_dir / "churn_events.csv")
        for row in churn_rows_csv:
            db.add(ChurnEvent(
                id=uuid.UUID(row.get("id") or row.get("event_id") or str(uuid.uuid4())),
                account_id=uuid.UUID(row["account_id"]) if row.get("account_id") else None,
                period=row.get("period", ""),
                event_type=row.get("event_type"),
                arr_change=float(row["arr_change"]) if row.get("arr_change") else None,
                reason=row.get("reason"),
                detected_at=_parse_datetime(row["detected_at"]) if row.get("detected_at") else _utc_now_naive(),
                created_at=_parse_datetime(row["created_at"]) if row.get("created_at") else _utc_now_naive(),
            ))
        counts["churn_events"] = len(churn_rows_csv)
        await db.flush()

        # B5: Sales units
        sales_unit_rows_csv = _read_csv_rows(company_dir / "sales_units.csv")
        for row in sales_unit_rows_csv:
            db.add(SalesUnit(
                id=uuid.UUID(row.get("id") or str(uuid.uuid4())),
                external_id=row.get("external_id"),
                opportunity_id=uuid.UUID(row["opportunity_id"]) if row.get("opportunity_id") else None,
                account_id=uuid.UUID(row["account_id"]) if row.get("account_id") else None,
                owner_user_id=uuid.UUID(row["owner_user_id"]) if row.get("owner_user_id") else None,
                booked_date=_parse_optional_date(row.get("booked_date")),
                amount=float(row["amount"]) if row.get("amount") else None,
                currency=row.get("currency"),
                source_system=row.get("source_system"),
                created_at=_parse_datetime(row["created_at"]) if row.get("created_at") else _utc_now_naive(),
            ))
        counts["sales_units"] = len(sales_unit_rows_csv)
        await db.flush()

        # Optional: line items when present
        suli_rows_csv = _read_csv_rows(company_dir / "sales_unit_line_items.csv")
        for row in suli_rows_csv:
            db.add(SalesUnitLineItem(
                id=uuid.UUID(row.get("id") or str(uuid.uuid4())),
                sales_unit_id=uuid.UUID(row["sales_unit_id"]),
                product_id=uuid.UUID(row["product_id"]) if row.get("product_id") else None,
                quantity=int(row["quantity"]) if row.get("quantity") else None,
                unit_price=float(row["unit_price"]) if row.get("unit_price") else None,
                net_amount=float(row["net_amount"]) if row.get("net_amount") else None,
                source_system=row.get("source_system"),
                created_at=_parse_datetime(row["created_at"]) if row.get("created_at") else _utc_now_naive(),
            ))
        counts["sales_unit_line_items"] = len(suli_rows_csv)
        await db.flush()

        # B5: Sales credits
        sales_credit_rows_csv = _read_csv_rows(company_dir / "sales_credits.csv")
        for row in sales_credit_rows_csv:
            db.add(SalesCredit(
                id=uuid.UUID(row.get("id") or str(uuid.uuid4())),
                sales_unit_id=uuid.UUID(row["sales_unit_id"]),
                user_id=uuid.UUID(row["user_id"]),
                credit_type=row.get("credit_type"),
                credit_percent=float(row["credit_percent"]) if row.get("credit_percent") else None,
                credit_amount=float(row["credit_amount"]) if row.get("credit_amount") else None,
                source_system=row.get("source_system"),
                created_at=_parse_datetime(row["created_at"]) if row.get("created_at") else _utc_now_naive(),
            ))
        counts["sales_credits"] = len(sales_credit_rows_csv)
        await db.flush()

        # Optional: persisted payout rows
        payout_rows_csv = _read_csv_rows(company_dir / "payouts.csv")
        for row in payout_rows_csv:
            db.add(PayoutRecord(
                id=uuid.UUID(row.get("id") or str(uuid.uuid4())),
                user_id=uuid.UUID(row["user_id"]),
                plan_id=uuid.UUID(row["plan_id"]) if row.get("plan_id") else None,
                period=row.get("period", ""),
                payout_amount=float(row["payout_amount"]) if row.get("payout_amount") else 0.0,
                commission_rate=float(row["commission_rate"]) if row.get("commission_rate") else None,
                fallback_used=_parse_bool(row.get("fallback_used"), default=False),
                confidence=float(row["confidence"]) if row.get("confidence") else None,
                source_system=row.get("source_system"),
                created_at=_parse_datetime(row["created_at"]) if row.get("created_at") else _utc_now_naive(),
            ))
        counts["payouts"] = len(payout_rows_csv)

        auto_repair = await _repair_loaded_quota_and_sales_credit_coverage(db)
        counts["auto_repair_quota_rows"] = auto_repair["quota_rows_added"]
        counts["auto_repair_sales_units"] = auto_repair["sales_units_added"]
        counts["auto_repair_sales_credits"] = auto_repair["sales_credits_added"]

        await db.commit()

    # Auto-seed cascade rules from loaded exec/VP/director users + plans
    counts["plan_cascade_rules"] = await _auto_seed_cascade_rules()
    return counts


async def _repair_loaded_quota_and_sales_credit_coverage(db) -> dict[str, int]:
    """Repair common data-quality gaps after CSV load using existing tables only.

    Repairs are idempotent:
    - Add quota rows for active reps that have revenue but no quota.
    - Add minimal SalesUnit/SalesCredit coverage for reps with closed-won deals
      whose mapped user has no SalesCredit rows.
    """
    from sqlalchemy import select as _sel, func as _func

    repaired = {
        "quota_rows_added": 0,
        "sales_units_added": 0,
        "sales_credits_added": 0,
    }

    # ── 1) Quota coverage for active reps ───────────────────────────────────
    missing_active_reps = (
        await db.execute(
            _sel(Rep.id, Rep.team_id)
            .outerjoin(Quota, Rep.id == Quota.rep_id)
            .outerjoin(Revenue, Rep.id == Revenue.rep_id)
            .group_by(Rep.id, Rep.team_id)
            .having(_func.count(Quota.id) == 0)
            .having(_func.coalesce(_func.sum(Revenue.amount), 0) > 0)
        )
    ).all()

    if missing_active_reps:
        periods = [
            p for (p,) in (await db.execute(_sel(Quota.period).distinct())).all()
            if p
        ]
        periods = sorted(periods)

        global_avg_rows = (
            await db.execute(
                _sel(Quota.period, _func.avg(Quota.amount))
                .group_by(Quota.period)
            )
        ).all()
        avg_by_period = {
            str(period): float(avg or 0.0)
            for period, avg in global_avg_rows
        }

        team_avg_rows = (
            await db.execute(
                _sel(Rep.team_id, Quota.period, _func.avg(Quota.amount))
                .join(Quota, Quota.rep_id == Rep.id)
                .group_by(Rep.team_id, Quota.period)
            )
        ).all()
        team_avg_by_period = {
            (team_id, str(period)): float(avg or 0.0)
            for team_id, period, avg in team_avg_rows
        }

        for rep_id, team_id in missing_active_reps:
            for period in periods:
                amount = team_avg_by_period.get((team_id, period), avg_by_period.get(period, 0.0))
                if amount <= 0:
                    continue
                db.add(
                    Quota(
                        rep_id=rep_id,
                        period=period,
                        amount=round(amount, 2),
                    )
                )
                repaired["quota_rows_added"] += 1

    if repaired["quota_rows_added"] > 0:
        await db.flush()

    # ── 2) SalesCredit coverage for reps with closed-won deals ─────────────
    reps_with_closed_won = [
        rep_id
        for (rep_id,) in (
            await db.execute(
                _sel(Deal.rep_id)
                .where(Deal.stage == "Closed Won", Deal.rep_id.isnot(None))
                .distinct()
            )
        ).all()
        if rep_id is not None
    ]

    if reps_with_closed_won:
        rep_ids_str = [str(rid) for rid in reps_with_closed_won]

        user_rows = (await db.execute(_sel(UserProfile.id, UserProfile.external_id))).all()
        rep_to_user_id: dict[str, uuid.UUID] = {}
        for user_id, external_id in user_rows:
            if not external_id or not str(external_id).startswith("USR-"):
                continue
            prefix = str(external_id)[4:]
            for rep_id_s in rep_ids_str:
                normalized_prefix = prefix.replace("-", "")
                normalized_rep_id = rep_id_s.replace("-", "")
                if rep_id_s.startswith(prefix) or normalized_rep_id.startswith(normalized_prefix):
                    rep_to_user_id[rep_id_s] = user_id
                    break

        users_with_credit = set(
            (await db.execute(_sel(SalesCredit.user_id).where(SalesCredit.user_id.isnot(None)).distinct())).scalars().all()
        )

        uncovered_rep_ids = [
            rep_id
            for rep_id in reps_with_closed_won
            if rep_to_user_id.get(str(rep_id)) is not None
            and rep_to_user_id.get(str(rep_id)) not in users_with_credit
        ]

        for rep_id in uncovered_rep_ids:
            user_id = rep_to_user_id.get(str(rep_id))
            if user_id is None:
                continue

            top_closed_won = (
                await db.execute(
                    _sel(Deal)
                    .where(Deal.rep_id == rep_id, Deal.stage == "Closed Won")
                    .order_by(Deal.amount.desc().nullslast(), Deal.actual_close_date.desc().nullslast())
                    .limit(1)
                )
            ).scalars().first()

            if top_closed_won is None:
                continue

            sales_unit = (
                await db.execute(
                    _sel(SalesUnit)
                    .where(SalesUnit.owner_user_id == user_id)
                    .order_by(SalesUnit.created_at.desc())
                    .limit(1)
                )
            ).scalars().first()

            if sales_unit is None:
                sales_unit = SalesUnit(
                    id=uuid.uuid4(),
                    external_id=f"AUTO-REPAIR-{str(top_closed_won.id)[:8]}-{str(user_id)[:8]}",
                    opportunity_id=top_closed_won.id,
                    account_id=top_closed_won.account_id,
                    owner_user_id=user_id,
                    booked_date=top_closed_won.actual_close_date or top_closed_won.expected_close_date or date.today(),
                    amount=float(top_closed_won.amount or 0.0),
                    currency="USD",
                    source_system="auto_repair_data_quality",
                    created_at=_utc_now_naive(),
                )
                db.add(sales_unit)
                repaired["sales_units_added"] += 1
                await db.flush()

            credit_exists = (
                await db.execute(
                    _sel(SalesCredit.id)
                    .where(SalesCredit.user_id == user_id)
                    .limit(1)
                )
            ).first()
            if credit_exists:
                continue

            base_amount = float(top_closed_won.amount or sales_unit.amount or 0.0)
            db.add(
                SalesCredit(
                    id=uuid.uuid4(),
                    sales_unit_id=sales_unit.id,
                    user_id=user_id,
                    credit_type="primary_ae",
                    credit_percent=1.0,
                    credit_amount=round(base_amount, 2),
                    source_system="auto_repair_data_quality",
                    created_at=_utc_now_naive(),
                )
            )
            repaired["sales_credits_added"] += 1

    if repaired["sales_units_added"] > 0 or repaired["sales_credits_added"] > 0:
        await db.flush()

    return repaired


def _period_to_months(period: str) -> list[str]:
    """Expand a canonical period label to YYYY-MM month labels.

    Supported:
    - YYYY-Q1..Q4
    - YYYY-MM
    - YYYY
    """
    normalized = str(period or "").strip()
    if not normalized:
        return []

    quarter_match = re.match(r"^(\d{4})-Q([1-4])$", normalized)
    if quarter_match:
        year = int(quarter_match.group(1))
        quarter = int(quarter_match.group(2))
        start_month = (quarter - 1) * 3 + 1
        return [f"{year:04d}-{m:02d}" for m in range(start_month, start_month + 3)]

    month_match = re.match(r"^(\d{4})-(\d{2})$", normalized)
    if month_match:
        month = int(month_match.group(2))
        if 1 <= month <= 12:
            return [normalized]
        return []

    year_match = re.match(r"^(\d{4})$", normalized)
    if year_match:
        year = int(year_match.group(1))
        return [f"{year:04d}-{m:02d}" for m in range(1, 13)]

    return []


async def _build_expected_payout_rows(db) -> list[dict[str, object]]:
    """Build expected payout rows from current DB state.

    This mirrors payout generation semantics and supports quarterly, monthly,
    and annual quota periods.
    """
    from backend.models import PlanAssignment, Rule as RuleModel
    from backend.payout import compute_payout
    from backend.payout.engine import PayoutConfig, build_payout_config_from_rules
    from sqlalchemy import func as _func
    from sqlalchemy import literal_column
    from sqlalchemy import select as _sel

    # ── 1) Rep → User mapping (via email) ─────────────────────────────────
    rep_rows = (await db.execute(_sel(Rep.id, Rep.email))).all()
    user_rows = (await db.execute(_sel(UserProfile.id, UserProfile.email))).all()

    email_to_user_id: dict[str, uuid.UUID] = {
        str(email).lower(): user_id
        for user_id, email in user_rows
        if email
    }
    rep_to_user_id: dict[uuid.UUID, uuid.UUID] = {}
    for rep_id, email in rep_rows:
        if not email:
            continue
        mapped_user_id = email_to_user_id.get(str(email).lower())
        if mapped_user_id is not None:
            rep_to_user_id[rep_id] = mapped_user_id

    # ── 2) Aggregate quotas by rep + period ───────────────────────────────
    quota_rows = (
        await db.execute(
            _sel(Quota.rep_id, Quota.period, _func.sum(Quota.amount).label("total"))
            .group_by(Quota.rep_id, Quota.period)
        )
    ).all()
    if not quota_rows:
        return []

    # ── 3) Aggregate revenue by rep + month ───────────────────────────────
    rev_rows = (
        await db.execute(
            _sel(Revenue.rep_id, Revenue.period, _func.sum(Revenue.amount).label("total"))
            .group_by(Revenue.rep_id, Revenue.period)
        )
    ).all()
    rev_by_rep_month: dict[uuid.UUID, dict[str, float]] = {}
    for rep_id, period, total in rev_rows:
        if rep_id is None or not period:
            continue
        month = str(period)[:7]
        if not re.match(r"^\d{4}-\d{2}$", month):
            continue
        bucket = rev_by_rep_month.setdefault(rep_id, {})
        bucket[month] = bucket.get(month, 0.0) + float(total or 0.0)

    # ── 4) Aggregate closed deal counts by rep + month ────────────────────
    close_month_expr = _func.to_char(Deal.actual_close_date, "YYYY-MM").label("close_month")
    deal_rows = (
        await db.execute(
            _sel(
                Deal.rep_id,
                close_month_expr,
                Deal.stage,
                _func.count().label("cnt"),
            )
            .where(Deal.stage.in_(["Closed Won", "Closed Lost"]))
            .where(Deal.actual_close_date.isnot(None))
            .group_by(Deal.rep_id, literal_column("2"), Deal.stage)
        )
    ).all()
    won_by_rep_month: dict[tuple[uuid.UUID, str], int] = {}
    lost_by_rep_month: dict[tuple[uuid.UUID, str], int] = {}
    for rep_id, close_month, stage, cnt in deal_rows:
        if rep_id is None or not close_month:
            continue
        key = (rep_id, str(close_month))
        if stage == "Closed Won":
            won_by_rep_month[key] = won_by_rep_month.get(key, 0) + int(cnt or 0)
        else:
            lost_by_rep_month[key] = lost_by_rep_month.get(key, 0) + int(cnt or 0)

    # ── 5) Deterministic user → plan mapping ──────────────────────────────
    plan_assignment_rows = (
        await db.execute(
            _sel(
                PlanAssignment.user_id,
                PlanAssignment.plan_id,
                PlanAssignment.effective_start_date,
                PlanAssignment.created_at,
            ).order_by(
                PlanAssignment.user_id,
                PlanAssignment.effective_start_date.desc().nullslast(),
                PlanAssignment.created_at.desc().nullslast(),
            )
        )
    ).all()
    user_to_plan_id: dict[uuid.UUID, uuid.UUID] = {}
    for user_id, plan_id, _effective_start, _created_at in plan_assignment_rows:
        if user_id not in user_to_plan_id:
            user_to_plan_id[user_id] = plan_id

    # ── 6) Plan configs from rule rows ─────────────────────────────────────
    rule_rows = (await db.execute(_sel(RuleModel))).scalars().all()
    rules_by_plan: dict[uuid.UUID, list] = {}
    for rule in rule_rows:
        rules_by_plan.setdefault(rule.plan_id, []).append(rule)

    config_by_plan: dict[uuid.UUID, PayoutConfig] = {
        plan_id: build_payout_config_from_rules(rules)
        for plan_id, rules in rules_by_plan.items()
    }

    # ── 7) Build expected rows ─────────────────────────────────────────────
    expected_rows: list[dict[str, object]] = []
    for rep_id, period, quota_total in quota_rows:
        if rep_id is None or not period:
            continue

        user_id = rep_to_user_id.get(rep_id)
        if user_id is None:
            continue

        period_label = str(period)
        months = _period_to_months(period_label)
        if not months:
            continue

        quota_amount = float(quota_total or 0.0)
        rep_months = rev_by_rep_month.get(rep_id, {})
        revenue_amount = sum(rep_months.get(month, 0.0) for month in months)
        deals_won = sum(won_by_rep_month.get((rep_id, month), 0) for month in months)
        deals_lost = sum(lost_by_rep_month.get((rep_id, month), 0) for month in months)

        plan_id = user_to_plan_id.get(user_id)
        config = config_by_plan.get(plan_id) if plan_id else None
        payout_result = compute_payout(revenue_amount, quota_amount, deals_won, deals_lost, config)

        expected_rows.append(
            {
                "user_id": user_id,
                "plan_id": plan_id,
                "period": period_label,
                "payout_amount": round(float(payout_result.get("payout") or 0.0), 2),
                "commission_rate": payout_result.get("commission_rate"),
                "fallback_used": bool(payout_result.get("fallback_used", False)),
                "confidence": (
                    float(payout_result.get("confidence"))
                    if payout_result.get("confidence") is not None
                    else None
                ),
                "source_system": "generated",
            }
        )

    expected_rows.sort(
        key=lambda row: (
            str(row["period"]),
            str(row["user_id"]),
            str(row.get("plan_id") or ""),
        )
    )
    return expected_rows


async def _validate_payout_math_consistency(payout_delta_tolerance: float = 0.01) -> dict[str, float | int]:
    """Validate stored payouts against engine-derived expected payouts for all reps."""
    from backend.models import PayoutRecord
    from sqlalchemy import select as _sel

    async with get_session_factory()() as db:
        expected_rows = await _build_expected_payout_rows(db)
        expected_by_key = {
            (row["user_id"], row["period"], row["plan_id"]): row
            for row in expected_rows
        }

        payout_rows = (
            await db.execute(
                _sel(
                    PayoutRecord.user_id,
                    PayoutRecord.period,
                    PayoutRecord.plan_id,
                    PayoutRecord.payout_amount,
                )
            )
        ).all()

        existing_by_key: dict[tuple[uuid.UUID, str, uuid.UUID | None], float] = {}
        duplicate_rows = 0
        for user_id, period, plan_id, payout_amount in payout_rows:
            key = (user_id, period, plan_id)
            if key in existing_by_key:
                duplicate_rows += 1
                continue
            existing_by_key[key] = float(payout_amount or 0.0)

        missing_rows = 0
        mismatched_rows = 0
        validated_rows = 0
        max_abs_delta = 0.0

        for key, expected in expected_by_key.items():
            stored_payout = existing_by_key.get(key)
            if stored_payout is None:
                missing_rows += 1
                continue

            validated_rows += 1
            expected_payout = float(expected["payout_amount"])
            delta = abs(stored_payout - expected_payout)
            if delta > max_abs_delta:
                max_abs_delta = delta
            if delta > payout_delta_tolerance:
                mismatched_rows += 1

        extra_rows = max(0, len(existing_by_key) - len(expected_by_key))

        return {
            "expected_rows": len(expected_by_key),
            "validated_rows": validated_rows,
            "missing_rows": missing_rows,
            "mismatched_rows": mismatched_rows,
            "duplicate_rows": duplicate_rows,
            "extra_rows": extra_rows,
            "reps_validated": len({str(row["user_id"]) for row in expected_rows}),
            "periods_validated": len({str(row["period"]) for row in expected_rows}),
            "max_abs_delta": round(max_abs_delta, 4),
        }


async def _seed_payout_records(company_dir: Path | None = None) -> int:
    """B2: Compute and persist PayoutRecord rows for each user × quota period.

    Uses rep→user mapping (by email) to satisfy the users.id FK on payouts.
    One row per (user_id, quarter, plan_id). Safe to call multiple times — skips
    existing (user_id, period, plan_id) pairs.

    Performance: all aggregations are pre-loaded in bulk so there are no
    per-row DB round-trips inside the loop.

    Payout calculation is plan-aware: each rep's assigned Plan + Rule rows are
    used to build a per-plan PayoutConfig via build_payout_config_from_rules().
    Deal counts are scoped to each quarter (not lifetime totals).

    If company_dir is given, also writes payouts.csv there.
    """
    from backend.models import PayoutRecord
    from sqlalchemy import select as _sel

    async with get_session_factory()() as db:
        expected_rows = await _build_expected_payout_rows(db)
        if not expected_rows:
            return 0

        expected_by_key = {
            (row["user_id"], row["period"], row["plan_id"]): row
            for row in expected_rows
        }
        expected_keys = set(expected_by_key.keys())

        existing_records = (
            await db.execute(
                _sel(PayoutRecord).order_by(PayoutRecord.created_at.asc(), PayoutRecord.id.asc())
            )
        ).scalars().all()

        existing_by_key: dict[tuple[uuid.UUID, str, uuid.UUID | None], PayoutRecord] = {}
        duplicate_records: list[PayoutRecord] = []
        for record in existing_records:
            key = (record.user_id, record.period, record.plan_id)
            if key in existing_by_key:
                duplicate_records.append(record)
            else:
                existing_by_key[key] = record

        stale_records = [
            record
            for key, record in existing_by_key.items()
            if key not in expected_keys
        ]

        changed_rows = 0

        if duplicate_records:
            for record in duplicate_records:
                await db.delete(record)
            changed_rows += len(duplicate_records)

        if stale_records:
            for record in stale_records:
                await db.delete(record)
                existing_by_key.pop((record.user_id, record.period, record.plan_id), None)
            changed_rows += len(stale_records)

        for key, expected in expected_by_key.items():
            record = existing_by_key.get(key)
            if record is None:
                db.add(
                    PayoutRecord(
                        user_id=expected["user_id"],
                        plan_id=expected["plan_id"],
                        period=expected["period"],
                        payout_amount=expected["payout_amount"],
                        commission_rate=expected.get("commission_rate"),
                        fallback_used=bool(expected.get("fallback_used", False)),
                        confidence=expected.get("confidence"),
                        source_system=str(expected.get("source_system") or "generated"),
                    )
                )
                changed_rows += 1
                continue

            row_changed = False

            expected_payout = float(expected["payout_amount"])
            if abs(float(record.payout_amount or 0.0) - expected_payout) > 0.01:
                record.payout_amount = expected_payout
                row_changed = True

            expected_rate = expected.get("commission_rate")
            actual_rate = float(record.commission_rate) if record.commission_rate is not None else None
            if expected_rate is None:
                if actual_rate is not None:
                    record.commission_rate = None
                    row_changed = True
            else:
                expected_rate_f = float(expected_rate)
                if actual_rate is None or abs(actual_rate - expected_rate_f) > 1e-6:
                    record.commission_rate = expected_rate_f
                    row_changed = True

            expected_fallback = bool(expected.get("fallback_used", False))
            if bool(record.fallback_used) != expected_fallback:
                record.fallback_used = expected_fallback
                row_changed = True

            expected_conf = expected.get("confidence")
            actual_conf = float(record.confidence) if record.confidence is not None else None
            if expected_conf is None:
                if actual_conf is not None:
                    record.confidence = None
                    row_changed = True
            else:
                expected_conf_f = float(expected_conf)
                if actual_conf is None or abs(actual_conf - expected_conf_f) > 1e-6:
                    record.confidence = expected_conf_f
                    row_changed = True

            if (record.source_system or "") != "generated":
                record.source_system = "generated"
                row_changed = True

            if row_changed:
                changed_rows += 1

        if changed_rows > 0:
            await db.commit()

        if company_dir is not None:
            payout_rows = (
                await db.execute(
                    _sel(PayoutRecord).order_by(PayoutRecord.period.asc(), PayoutRecord.user_id.asc())
                )
            ).scalars().all()
            payout_csv_rows = [
                {
                    "id": str(r.id),
                    "user_id": str(r.user_id),
                    "plan_id": str(r.plan_id) if r.plan_id else "",
                    "period": r.period,
                    "payout_amount": str(r.payout_amount),
                    "commission_rate": str(r.commission_rate) if r.commission_rate is not None else "",
                    "fallback_used": str(r.fallback_used).lower(),
                    "confidence": str(r.confidence) if r.confidence is not None else "",
                    "source_system": r.source_system or "generated",
                    "created_at": r.created_at.isoformat() if r.created_at else "",
                }
                for r in payout_rows
            ]
            _write_rows_csv(company_dir / "payouts.csv", payout_csv_rows)

        return changed_rows


async def _auto_seed_cascade_rules() -> int:
    """After a company load, create PlanCascadeRule rows for exec/VP/director users.

    - Upgrades plans to global/team scope and assigns them to high-rank users.
    - Creates one cascade rule per (plan, owner) pairing so the Org Hierarchy
      page has real data to display.
    - Safe to call repeatedly — skips pairs that already have a rule.
    """
    from backend.models import POSITION_RANK_EXECUTIVE, POSITION_RANK_VP, POSITION_RANK_DIRECTOR  # local import to avoid circularity
    from backend.models import Plan as _Plan, PlanCascadeRule as _PCR, UserProfile as _UP, Position as _Pos
    Plan, PlanCascadeRule, UserProfile, Position = _Plan, _PCR, _UP, _Pos
    from sqlalchemy import select as _select
    select = _select
    async with get_session_factory()() as db:
        # Find users with rank <= director
        user_pos_rows = (
            await db.execute(
                select(UserProfile, Position)
                .join(Position, UserProfile.position_id == Position.id)
                .where(Position.rank <= POSITION_RANK_DIRECTOR)
                .order_by(Position.rank)
            )
        ).all()
        if not user_pos_rows:
            return 0

        plans = (await db.execute(select(Plan))).scalars().all()
        if not plans:
            return 0

        # Assign scope to plans based on owner rank and position in list
        scope_map = {
            POSITION_RANK_EXECUTIVE: "global",
            POSITION_RANK_VP: "department",
            POSITION_RANK_DIRECTOR: "team",
        }

        created = 0
        for i, (user, pos) in enumerate(user_pos_rows):
            # Each leader gets one plan (cycle through plans)
            plan = plans[i % len(plans)]
            scope = scope_map.get(pos.rank, "team")
            priority = pos.rank * 10  # exec=10, vp=20, director=30

            # Update plan scope and owner if not already set
            if plan.owner_user_id is None:
                plan.owner_user_id = user.id
                plan.scope = scope

            # Check for existing rule
            existing = (
                await db.execute(
                    select(PlanCascadeRule).where(
                        PlanCascadeRule.plan_id == plan.id,
                        PlanCascadeRule.owner_user_id == user.id,
                    )
                )
            ).scalar_one_or_none()
            if existing:
                continue

            db.add(PlanCascadeRule(
                plan_id=plan.id,
                owner_user_id=user.id,
                cascade_scope="all_reports",
                min_rank=pos.rank + 1,  # cascade to ranks below the owner
                max_rank=99,
                priority=priority,
            ))
            created += 1

        await db.commit()
        return created


async def seed(
    n_reps: int = 12,
    n_accounts: int = 60,
    n_deals: int = 150,
    months: int = 18,
    company_name: str = "default-company",
    n_products: int = 5,
    n_plans: int = 4,
    n_rules: int = 4,
    n_territories: int = 4,
    n_subregions_per_territory: int = 4,
    target_total_revenue: float | None = None,
    include_org_hierarchy: bool = True,
    archetype: str = "saas_enterprise",
    max_quota_to_revenue_ratio: float = 20.0,
    min_open_deal_activity_coverage_pct: float = 50.0,
    manager_span_warn_threshold: int = 8,
    rep_quota_revenue_outlier_threshold: float = 5.0,
):
    """Generate complete dataset, persist to CSV files, then load those CSVs into DB."""
    dataset = _generate_dataset(
        n_reps=n_reps,
        n_accounts=n_accounts,
        n_deals=n_deals,
        months=months,
        n_products=n_products,
        target_total_revenue=target_total_revenue,
        archetype=archetype,
        company_name=company_name,
    )
    company_dir = _write_dataset_to_csv(dataset, company_name=company_name)
    extension_tables = _build_saas_extension_tables(
        dataset=dataset,
        n_plans=n_plans,
        n_rules=n_rules,
        n_products=n_products,
        n_territories=n_territories,
        n_subregions_per_territory=n_subregions_per_territory,
        include_org_hierarchy=include_org_hierarchy,
        archetype=archetype,
    )
    extension_files = _write_extension_tables(company_dir, extension_tables)
    validator = RevOpsBusinessRuleValidator(
        max_quota_to_revenue_ratio=max_quota_to_revenue_ratio,
        min_open_deal_activity_coverage_pct=min_open_deal_activity_coverage_pct,
        manager_span_warn_threshold=manager_span_warn_threshold,
    )
    validation_result = validator.validate(company_dir)
    validation_summary = validation_result.summary()
    if not validation_result.passed:
        top_issues = "; ".join(v.message for v in validation_result.violations[:3])
        raise ValueError(f"RevOps validation failed for generated dataset: {top_issues}")

    reconciliation_snapshot = _build_revops_reconciliation_snapshot(
        dataset,
        extension_tables,
        rep_quota_revenue_outlier_threshold=rep_quota_revenue_outlier_threshold,
    )
    ingestion_runs = _collect_csv_ingestion_runs(company_dir, extra_tables=list(extension_tables.keys()))
    # Remove stale payouts.csv before loading so old user_id FKs don't cause
    # constraint violations. _seed_payout_records will write a fresh payouts.csv.
    _stale_payouts_csv = company_dir / "payouts.csv"
    if _stale_payouts_csv.exists():
        _stale_payouts_csv.unlink()
    # One company is generated here, so the load, the payout recompute and the
    # validation below all run under its tenant: inserts are stamped with it and
    # every read sees only its rows.
    with tenant_scope(company_name):
        counts = await _load_csvs_into_database(company_dir, company_id=company_name)
        payout_count = await _seed_payout_records(company_dir=company_dir)  # B2
        counts["payouts"] = payout_count

        payout_validation = await _validate_payout_math_consistency(payout_delta_tolerance=0.01)
        counts["payout_math_expected_rows"] = int(payout_validation["expected_rows"])
        counts["payout_math_rows_validated"] = int(payout_validation["validated_rows"])
        counts["payout_math_reps_validated"] = int(payout_validation["reps_validated"])
        counts["payout_math_periods_validated"] = int(payout_validation["periods_validated"])
        counts["payout_math_missing_rows"] = int(payout_validation["missing_rows"])
        counts["payout_math_mismatched_rows"] = int(payout_validation["mismatched_rows"])
        counts["payout_math_duplicate_rows"] = int(payout_validation["duplicate_rows"])
        counts["payout_math_extra_rows"] = int(payout_validation["extra_rows"])
        counts["payout_math_max_abs_delta"] = float(payout_validation["max_abs_delta"])

        if (
            counts["payout_math_missing_rows"] > 0
            or counts["payout_math_mismatched_rows"] > 0
            or counts["payout_math_duplicate_rows"] > 0
        ):
            raise ValueError(
                "Automated payout math validation failed for generated dataset: "
                f"missing={counts['payout_math_missing_rows']}, "
                f"mismatched={counts['payout_math_mismatched_rows']}, "
                f"duplicates={counts['payout_math_duplicate_rows']}, "
                f"max_delta=${counts['payout_math_max_abs_delta']:.4f}"
            )

        audit_path = _write_ingestion_audit(
            company_dir,
            company_name=company_name,
            db_counts=counts,
            runs=ingestion_runs,
            validation_summary=validation_summary,
            reconciliation_snapshot=reconciliation_snapshot,
        )

        # ── Automated payout + ML forecast audit ────────────────────────────
        from backend.audit.payout_audit import audit_company as _audit_company
        _audit_report = _audit_company(company_dir)
        _audit_status = "PASSED" if _audit_report.passed else f"FAILED ({'; '.join(_audit_report.errors)})"

        print(f"✓ Generated CSV dataset under: {company_dir}")
        print(
            "✓ Loaded to DB from CSVs: "
            f"{counts.get('teams', 0)} teams, {counts.get('reps', 0)} reps, "
            f"{counts.get('accounts', 0)} accounts, {counts.get('deals', 0)} deals"
        )
        print(f"✓ Extended SaaS tables generated: {', '.join(extension_files)}")
        if target_total_revenue is not None:
            total_generated_revenue = sum(float(r.get("amount", 0) or 0) for r in dataset.get("revenue", []))
            print(f"✓ Revenue target: ${target_total_revenue:,.2f} (generated ${total_generated_revenue:,.2f})")
        print(f"✓ Ingestion audit written: {audit_path}")
        print(
            "✓ Payout math validation: "
            f"{counts['payout_math_rows_validated']}/{counts['payout_math_expected_rows']} rows validated, "
            f"max delta ${counts['payout_math_max_abs_delta']:.4f}"
        )
        print(f"✓ Payout+forecast audit: {_audit_status} "
              f"({_audit_report.payout.reps_ok} OK, "
              f"{_audit_report.payout.reps_exempt} exempt, "
              f"{_audit_report.payout.reps_with_bugs} bugs | "
              f"forecast next-Q: ${_audit_report.forecast.company_forecast_next_q:,.0f})")


# ── CRM Auto-Scaffold ──────────────────────────────────────────────────────
# When a company directory was created from CRM export (Salesforce, HubSpot,
# etc.), it typically only has core tables (teams, reps, accounts, deals,
# revenue, quotas, activities).  This function detects missing extension tables
# and generates them from the existing core data so that the compensation
# engine, org hierarchy, and payout pipeline work end-to-end.

_EXTENSION_TABLES_REQUIRED = [
    "positions", "users", "managers", "plans", "rules",
    "plan_assignments", "rep_hierarchy", "sales_units", "sales_credits",
]


def _scaffold_missing_extension_tables(
    company_dir: Path,
    archetype: str = "saas_enterprise",
    n_plans: int = 2,
    n_products: int = 4,
    n_territories: int = 3,
    n_subregions_per_territory: int = 2,
) -> list[str]:
    """Auto-generate missing extension CSVs from existing core data.

    Returns list of table names that were scaffolded.
    """
    # Check which extension tables already exist with data
    missing = []
    for table in _EXTENSION_TABLES_REQUIRED:
        csv_path = company_dir / f"{table}.csv"
        rows = _read_csv_rows(csv_path)
        if not rows:
            missing.append(table)

    if not missing:
        return []  # All extension tables already present

    # Read core dataset from existing CSVs
    dataset: dict[str, list[dict]] = {}
    for table in TABLE_ORDER:
        dataset[table] = _read_csv_rows(company_dir / f"{table}.csv")

    if not dataset.get("reps"):
        return []  # Cannot scaffold without reps

    # Generate extension tables from core data
    extension_tables = _build_saas_extension_tables(
        dataset=dataset,
        n_plans=n_plans,
        n_rules=4,  # standard 4-tier for SaaS, 6-tier for insurance
        n_products=n_products,
        n_territories=n_territories,
        n_subregions_per_territory=n_subregions_per_territory,
        include_org_hierarchy=True,
        archetype=archetype,
    )

    # Only write tables that were missing — don't overwrite user-provided data
    scaffolded: list[str] = []
    for table in missing:
        rows = extension_tables.get(table, [])
        if rows:
            csv_path = company_dir / f"{table}.csv"
            _write_rows_csv(csv_path, rows)
            scaffolded.append(table)

    # Also scaffold dependent tables that might be missing
    for extra in ["products", "territories", "user_territory_assignments",
                   "rep_product_assignments", "rep_ramp", "bookings",
                   "attainment_snapshots", "arr_waterfall", "churn_events"]:
        csv_path = company_dir / f"{extra}.csv"
        existing = _read_csv_rows(csv_path)
        if not existing and extra in extension_tables:
            rows = extension_tables[extra]
            if rows:
                _write_rows_csv(csv_path, rows)
                scaffolded.append(extra)

    return scaffolded


async def load_company_dataset(company_name: str, base_dir: str = "companies") -> dict[str, int]:
    """Load an existing companies/<company_name> CSV dataset into DB."""
    company_dir = Path(base_dir) / _safe_company_dir_name(company_name)
    if not company_dir.exists() or not company_dir.is_dir():
        raise FileNotFoundError(f"Company dataset folder not found: {company_dir}")

    # Auto-scaffold missing extension tables for CRM imports
    scaffolded = _scaffold_missing_extension_tables(company_dir)
    import logging as _log
    if scaffolded:
        _log.getLogger(__name__).info(
            "Auto-scaffolded %d missing tables for %s: %s",
            len(scaffolded), company_name, ", ".join(scaffolded),
        )

    integrity_errors = _validate_company_csv_integrity(company_dir)
    if integrity_errors:
        raise ValueError("; ".join(integrity_errors))

    normalized_company = (company_name or "").strip()

    # The load and everything after it belong to one company, so they share one
    # tenant scope: inserts are stamped with it, and the payout recompute,
    # validation and audit below see only what was just loaded rather than every
    # tenant in the database.
    with tenant_scope(normalized_company):
        counts = await _load_csvs_into_database(company_dir, company_id=normalized_company)
        if scaffolded:
            counts["auto_scaffolded_tables"] = scaffolded

        # Recompute payout records using plan-aware engine so imported companies
        # with their own Plan/Rule data get accurate commission calculations.
        payout_count = await _seed_payout_records(company_dir=None)
        if payout_count:
            counts["payouts_recomputed"] = payout_count

        payout_validation = await _validate_payout_math_consistency(payout_delta_tolerance=0.01)
        counts["payout_math_expected_rows"] = int(payout_validation["expected_rows"])
        counts["payout_math_rows_validated"] = int(payout_validation["validated_rows"])
        counts["payout_math_reps_validated"] = int(payout_validation["reps_validated"])
        counts["payout_math_periods_validated"] = int(payout_validation["periods_validated"])
        counts["payout_math_missing_rows"] = int(payout_validation["missing_rows"])
        counts["payout_math_mismatched_rows"] = int(payout_validation["mismatched_rows"])
        counts["payout_math_duplicate_rows"] = int(payout_validation["duplicate_rows"])
        counts["payout_math_extra_rows"] = int(payout_validation["extra_rows"])
        counts["payout_math_max_abs_delta"] = float(payout_validation["max_abs_delta"])

        if (
            counts["payout_math_missing_rows"] > 0
            or counts["payout_math_mismatched_rows"] > 0
            or counts["payout_math_duplicate_rows"] > 0
        ):
            raise ValueError(
                "Automated payout math validation failed after company load: "
                f"missing={counts['payout_math_missing_rows']}, "
                f"mismatched={counts['payout_math_mismatched_rows']}, "
                f"duplicates={counts['payout_math_duplicate_rows']}, "
                f"max_delta=${counts['payout_math_max_abs_delta']:.4f}"
            )

        # ── Automated payout + ML forecast audit ────────────────────────────
        from backend.audit.payout_audit import audit_company as _audit_company
        _audit_report = _audit_company(Path(base_dir) / _safe_company_dir_name(company_name))
        counts["audit_passed"] = int(_audit_report.passed)
        counts["audit_reps_with_bugs"] = _audit_report.payout.reps_with_bugs

        return counts


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate synthetic sales data CSVs and load them into DB")
    parser.add_argument("--company-name", default="default-company", help="Company folder name under companies/")
    parser.add_argument("--n-reps", type=int, default=12)
    parser.add_argument("--n-accounts", type=int, default=60)
    parser.add_argument("--n-deals", type=int, default=150)
    parser.add_argument(
        "--months", type=int, default=36,
        help="Months of history. The forecasting ensemble needs 24+; below that\n"
             "it falls back to a linear trend and drift monitoring reports\n"
             "insufficient_history, so the ML features never actually run.",
    )
    parser.add_argument("--n-products", type=int, default=5)
    parser.add_argument("--n-plans", type=int, default=4)
    parser.add_argument("--n-rules", type=int, default=4)
    parser.add_argument("--n-territories", type=int, default=4)
    parser.add_argument("--n-subregions-per-territory", type=int, default=4)
    parser.add_argument("--target-total-revenue", type=float, default=None)
    parser.set_defaults(include_org_hierarchy=True)
    parser.add_argument("--include-org-hierarchy", action="store_true", dest="include_org_hierarchy", help="Generate org hierarchy and enterprise role tables")
    parser.add_argument("--no-org-hierarchy", action="store_false", dest="include_org_hierarchy", help="Disable org hierarchy generation")
    parser.add_argument(
        "--archetype",
        default="saas_enterprise",
        choices=list(ARCHETYPE_PROFILES.keys()),
        help="Company archetype profile (controls deal size, quota, churn rates)",
    )
    parser.add_argument(
        "--max-quota-to-revenue-ratio",
        type=float,
        default=20.0,
        help="RevOps hard-fail threshold for total quota/total revenue ratio",
    )
    parser.add_argument(
        "--min-open-deal-activity-coverage-pct",
        type=float,
        default=50.0,
        help="RevOps hard-fail minimum percentage of open deals that must have activities",
    )
    parser.add_argument(
        "--manager-span-warn-threshold",
        type=int,
        default=8,
        help="RevOps warning threshold for max direct reports per manager",
    )
    parser.add_argument(
        "--rep-quota-revenue-outlier-threshold",
        type=float,
        default=5.0,
        help="Reconciliation threshold for counting per-rep quota/revenue outliers",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    asyncio.run(
        seed(
            n_reps=args.n_reps,
            n_accounts=args.n_accounts,
            n_deals=args.n_deals,
            months=args.months,
            company_name=args.company_name,
            n_products=args.n_products,
            n_plans=args.n_plans,
            n_rules=args.n_rules,
            n_territories=args.n_territories,
            n_subregions_per_territory=args.n_subregions_per_territory,
            target_total_revenue=args.target_total_revenue,
            include_org_hierarchy=args.include_org_hierarchy,
            archetype=args.archetype,
            max_quota_to_revenue_ratio=args.max_quota_to_revenue_ratio,
            min_open_deal_activity_coverage_pct=args.min_open_deal_activity_coverage_pct,
            manager_span_warn_threshold=args.manager_span_warn_threshold,
            rep_quota_revenue_outlier_threshold=args.rep_quota_revenue_outlier_threshold,
        )
    )
