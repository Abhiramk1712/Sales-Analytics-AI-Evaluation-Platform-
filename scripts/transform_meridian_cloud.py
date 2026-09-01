"""
scripts/transform_meridian_cloud.py
====================================
Transforms 'companies/meridian-cloud copy/' (enterprise ICM schema) into
the canonical format expected by load_company_dataset, writing output to
'companies/meridian-cloud/'.

Run:
    python -m scripts.transform_meridian_cloud
"""
from __future__ import annotations

import csv
import random
import uuid
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

random.seed(42)

SRC = Path("companies/meridian-cloud copy")
DST = Path("companies/meridian-cloud")

AMOUNT_SCALE = 1 / 1000  # brings $5.5B deals → $5.5M range

ACT_TYPES = ["call", "email", "demo", "meeting", "proposal"]
ACT_OUTCOMES = ["positive", "neutral", "no_response", "negative"]


# ── helpers ──────────────────────────────────────────────────────────────────

def _read(filename: str) -> list[dict[str, str]]:
    p = SRC / filename
    if not p.exists():
        return []
    with open(p, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _write(filename: str, rows: list[dict]) -> None:
    p = DST / filename
    if not rows:
        p.write_text("", encoding="utf-8")
        return
    fieldnames = list(rows[0].keys())
    with open(p, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)
    print(f"  wrote {len(rows):>6} rows → {filename}")


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _safe_float(val: str, default: float = 0.0) -> float:
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


def _safe_date(val: str) -> str:
    """Return YYYY-MM-DD or '' if parse fails."""
    if not val:
        return ""
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(val[:19], fmt[:len(val[:19])]).date().isoformat()
        except ValueError:
            continue
    return val[:10]


def _map_size_to_employee_count(size: str) -> str:
    mapping = {"SMB": "100", "MID": "500", "MIDMARKET": "500", "ENTERPRISE": "5000",
                "LARGE": "2000", "SMALL": "50"}
    return mapping.get((size or "").upper(), "250")


def _map_size_to_annual_revenue(size: str) -> str:
    mapping = {"SMB": "5000000", "MID": "50000000", "MIDMARKET": "50000000",
                "ENTERPRISE": "500000000", "LARGE": "200000000", "SMALL": "1000000"}
    return mapping.get((size or "").upper(), "20000000")


def _infer_stage(row: dict) -> tuple[str, str]:
    """Return (stage_name, close_probability)."""
    is_won = str(row.get("is_won", "")).lower() in ("true", "1", "yes")
    is_closed = str(row.get("is_closed", "")).lower() in ("true", "1", "yes")
    src_stage = (row.get("stage") or "").strip()
    if is_won:
        return "Closed Won", "100"
    if is_closed:
        return "Closed Lost", "0"
    stage_map = {
        "prospecting": ("Prospecting", "10"),
        "qualification": ("Qualification", "20"),
        "value proposition": ("Value Proposition", "50"),
        "proposal": ("Proposal / Price Quote", "60"),
        "negotiation": ("Negotiation / Review", "75"),
        "id decision makers": ("Id. Decision Makers", "40"),
        "perception analysis": ("Perception Analysis", "35"),
        "needs analysis": ("Needs Analysis", "25"),
    }
    return stage_map.get(src_stage.lower(), ("Prospecting", "10"))


def _infer_rank(job_role: str) -> int:
    jrl = (job_role or "").lower()
    if any(k in jrl for k in ("cro", "ceo", "chief")):
        return 1
    if any(k in jrl for k in ("svp", "vp", "vice president")):
        return 2
    if "director" in jrl:
        return 3
    if "manager" in jrl or "mgr" in jrl:
        return 4
    return 5


_RANK_LABELS = {1: "Executive", 2: "VP", 3: "Director", 4: "Manager", 5: "IC"}


def _quarter_label(year: str, month: str) -> str:
    try:
        m = int(month)
        y = int(year)
        q = (m - 1) // 3 + 1
        return f"{y}-Q{q}"
    except (ValueError, TypeError):
        return ""


# ── main transform ────────────────────────────────────────────────────────────

def transform():
    DST.mkdir(parents=True, exist_ok=True)
    print(f"Transforming: {SRC} → {DST}")

    # ── 1. teams.csv (derive from distinct regions in users) ─────────────────
    src_users = _read("users.csv")
    regions = sorted({u.get("region", "").strip() for u in src_users if u.get("region")})
    if not regions:
        regions = ["North America", "EMEA", "APAC"]
    team_by_region: dict[str, str] = {}
    teams_rows = []
    for region in regions:
        tid = str(uuid.uuid5(uuid.NAMESPACE_URL, f"meridian-cloud-team::{region}"))
        team_by_region[region] = tid
        teams_rows.append({"id": tid, "name": f"{region} Sales Team", "region": region})
    _write("teams.csv", teams_rows)

    # ── 2. reps.csv (from users.csv) ─────────────────────────────────────────
    # external_id in users.csv ↔ userId in payouts.csv
    ext_id_to_uuid: dict[str, str] = {}   # "200000" → UUID string
    reps_rows = []
    for u in src_users:
        uid = str(u.get("id") or u.get("user_id") or "").strip()
        if not uid:
            uid = str(uuid.uuid4())
        # Validate UUID
        try:
            uuid.UUID(uid)
        except ValueError:
            uid = str(uuid.uuid5(uuid.NAMESPACE_URL, f"meridian-user::{uid}"))

        ext_id = str(u.get("external_id") or "").strip()
        if ext_id:
            ext_id_to_uuid[ext_id] = uid

        first = str(u.get("first_name") or "").strip()
        last = str(u.get("last_name") or "").strip()
        name = f"{first} {last}".strip() or f"Rep {ext_id}"
        email = str(u.get("email_address") or u.get("email") or "").strip()
        region = str(u.get("region") or "").strip()
        hire_raw = str(u.get("hired_date") or u.get("effective_start_date") or "").strip()
        hire_date = _safe_date(hire_raw)
        team_id = team_by_region.get(region, teams_rows[0]["id"] if teams_rows else "")
        reps_rows.append({
            "id": uid,
            "team_id": team_id,
            "name": name,
            "email": email,
            "region": region,
            "hire_date": hire_date,
        })
    _write("reps.csv", reps_rows)
    rep_id_by_ext: dict[str, str] = ext_id_to_uuid  # ext_id → rep UUID

    # ── 3. accounts.csv ──────────────────────────────────────────────────────
    src_accounts = _read("accounts.csv")
    accounts_rows = []
    for a in src_accounts:
        aid = str(a.get("id") or "").strip()
        try:
            uuid.UUID(aid)
        except ValueError:
            aid = str(uuid.uuid5(uuid.NAMESPACE_URL, f"meridian-account::{aid}"))
        accounts_rows.append({
            "id": aid,
            "name": str(a.get("name") or "").strip(),
            "industry": str(a.get("industry") or "").strip(),
            "employee_count": _map_size_to_employee_count(str(a.get("size") or "")),
            "annual_revenue": _map_size_to_annual_revenue(str(a.get("size") or "")),
        })
    _write("accounts.csv", accounts_rows)
    account_id_set = {a["id"] for a in accounts_rows}

    # ── 4. deals.csv (from opportunity.csv) ──────────────────────────────────
    src_opps = _read("opportunity.csv")
    deals_rows = []
    deal_id_set: set[str] = set()
    for o in src_opps:
        opp_id_raw = str(o.get("opportunity_id") or o.get("id") or "").strip()
        # Convert non-UUID opportunity_id to UUID5
        try:
            did = str(uuid.UUID(opp_id_raw))
        except ValueError:
            did = str(uuid.uuid5(uuid.NAMESPACE_URL, f"meridian-opp::{opp_id_raw}"))

        account_id_raw = str(o.get("account_id") or "").strip()
        try:
            uuid.UUID(account_id_raw)
            account_id = account_id_raw
        except ValueError:
            account_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"meridian-account::{account_id_raw}"))
        if account_id not in account_id_set:
            # Use first available account
            account_id = accounts_rows[0]["id"] if accounts_rows else str(uuid.uuid4())

        owner_raw = str(o.get("owner_user_id") or "").strip()
        try:
            uuid.UUID(owner_raw)
            rep_id = owner_raw
        except ValueError:
            rep_id = rep_id_by_ext.get(owner_raw, reps_rows[0]["id"] if reps_rows else str(uuid.uuid4()))

        # Scale amount
        amount_raw = _safe_float(o.get("amount", "0"))
        amount = round(amount_raw * AMOUNT_SCALE, 2)

        stage, prob = _infer_stage(o)
        created_date = _safe_date(str(o.get("created_date") or ""))
        close_date = _safe_date(str(o.get("close_date") or ""))
        is_closed = str(o.get("is_closed", "")).lower() in ("true", "1", "yes")

        deals_rows.append({
            "id": did,
            "account_id": account_id,
            "rep_id": rep_id,
            "name": f"Opportunity {opp_id_raw[:16]}",
            "product": "",
            "stage": stage,
            "amount": str(amount),
            "close_probability": prob,
            "expected_close_date": close_date,
            "actual_close_date": close_date if is_closed else "",
            "created_at": (created_date + "T00:00:00") if created_date else _now(),
        })
        deal_id_set.add(did)
    _write("deals.csv", deals_rows)

    # ── 5. quotas.csv & revenue.csv (from payouts.csv) ───────────────────────
    src_payouts = _read("payouts.csv")
    # Aggregate by (userId, period_year, period_month) for revenue
    # Aggregate by (userId, quarter) for quotas
    rev_by_user_month: dict[tuple[str, str], float] = {}
    quota_by_user_quarter: dict[tuple[str, str], float] = {}

    for row in src_payouts:
        ext_id = str(row.get("userId") or "").strip()
        rep_id = rep_id_by_ext.get(ext_id)
        if not rep_id:
            continue
        year = str(row.get("period_year") or "").strip()
        month = str(row.get("period_month") or "").strip().zfill(2)
        period_month = f"{year}-{month}"
        rev_raw = _safe_float(row.get("monthly_revenue") or "0")
        rev_scaled = round(rev_raw * AMOUNT_SCALE, 2)
        if rev_scaled > 0:
            key = (rep_id, period_month)
            rev_by_user_month[key] = rev_by_user_month.get(key, 0.0) + rev_scaled

        # Quota: use annual_quota from users / 12 per month
        # (payouts.monthly_quota is often 0; use users.annual_quota instead)
        q_label = _quarter_label(year, row.get("period_month") or "1")
        if q_label:
            q_key = (rep_id, q_label)
            if q_key not in quota_by_user_quarter:
                quota_by_user_quarter[q_key] = 0.0  # will fill from users below

    # Collect all quarters present
    all_quarters = sorted({q for _, q in quota_by_user_quarter.keys()})
    if not all_quarters:
        # Generate last 4 quarters
        today = date.today()
        for i in range(4):
            m = today.month - (i * 3)
            y = today.year
            while m <= 0:
                m += 12
                y -= 1
            all_quarters.append(f"{y}-Q{(int(m)-1)//3+1}")
        all_quarters = sorted(set(all_quarters))

    # Derive quarterly quota from actual revenue: target ~75% average attainment
    quarterly_rev_by_rep: dict[str, dict[str, float]] = {}
    for (rid_r, period_month), rev_amt in rev_by_user_month.items():
        try:
            yr, mo = period_month.split("-")
            q = f"{yr}-Q{(int(mo)-1)//3+1}"
        except ValueError:
            continue
        quarterly_rev_by_rep.setdefault(rid_r, {})[q] = (
            quarterly_rev_by_rep.get(rid_r, {}).get(q, 0.0) + rev_amt
        )

    quotas_rows = []
    for rep in reps_rows:
        rid = rep["id"]
        rep_q_revs = quarterly_rev_by_rep.get(rid, {})
        avg_q_rev = sum(rep_q_revs.values()) / len(rep_q_revs) if rep_q_revs else 50000.0
        # Quota = avg quarterly revenue / 0.75 so average attainment ≈ 75%
        quarterly_quota = round(max(avg_q_rev / 0.75, 25000.0), 2)
        for q_label in all_quarters:
            quotas_rows.append({"rep_id": rid, "period": q_label, "amount": str(quarterly_quota)})
    _write("quotas.csv", quotas_rows)

    revenue_rows = []
    for (rep_id, period), amount in rev_by_user_month.items():
        if amount != 0:
            revenue_rows.append({
                "rep_id": rep_id,
                "period": period,
                "amount": str(amount),
                "account_id": "",
                "deal_id": "",
                "revenue_type": "new_logo",
                "contract_term_months": "12",
                "recognition_start_date": period + "-01",
            })
    _write("revenue.csv", revenue_rows)

    # ── 6. activities.csv (synthetic from deals) ──────────────────────────────
    activities_rows = []
    for deal in deals_rows:
        n = random.randint(2, 6)
        close_dt = deal.get("created_at", "")[:10]
        for _ in range(n):
            activities_rows.append({
                "id": str(uuid.uuid4()),
                "deal_id": deal["id"],
                "rep_id": deal["rep_id"],
                "type": random.choice(ACT_TYPES),
                "outcome": random.choice(ACT_OUTCOMES),
                "notes": "Activity log",
                "activity_date": (close_dt + "T00:00:00") if close_dt else _now(),
            })
    _write("activities.csv", activities_rows)

    # ── 7. positions.csv ─────────────────────────────────────────────────────
    src_positions = _read("positions.csv")
    pos_id_by_src: dict[str, str] = {}   # src id → canonical id
    positions_rows = []
    for p in src_positions:
        pid_raw = str(p.get("id") or "").strip()
        try:
            pid = str(uuid.UUID(pid_raw))
        except ValueError:
            pid = str(uuid.uuid5(uuid.NAMESPACE_URL, f"meridian-pos::{pid_raw}"))
        pos_id_by_src[pid_raw] = pid
        job_role = str(p.get("job_role") or p.get("name") or "").strip()
        rank = _infer_rank(job_role)
        positions_rows.append({
            "id": pid,
            "external_id": str(p.get("external_id") or pid_raw),
            "name": str(p.get("name") or p.get("title") or job_role),
            "level": str(p.get("position_tier") or "IC"),
            "rank": str(rank),
            "rank_label": _RANK_LABELS.get(rank, "IC"),
            "source_system": "meridian-cloud",
            "created_at": _now(),
            "effective_start_date": _safe_date(str(p.get("effective_start_date") or "")),
            "effective_end_date": "",
        })
    _write("positions.csv", positions_rows)

    # ── 8. users.csv (canonical UserProfile from source users) ───────────────
    # Map source position_id to canonical position UUID
    users_canonical = []
    pos_id_by_name: dict[str, str] = {p["name"]: p["id"] for p in positions_rows}
    for rep in reps_rows:
        # Find source user row
        src_u = next((u for u in src_users if (u.get("id") or u.get("user_id", "")) == rep["id"]), None)
        pos_id_raw = str(src_u.get("position_id") or "") if src_u else ""
        pos_id = pos_id_by_src.get(pos_id_raw)
        if not pos_id and positions_rows:
            job_role = str(src_u.get("job_role") or "") if src_u else ""
            pos_id = next((p["id"] for p in positions_rows if p["name"].lower() == job_role.lower()), positions_rows[0]["id"])
        users_canonical.append({
            "id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"meridian-userprofile::{rep['id']}")),
            "external_id": f"USR-{rep['id'][:8]}",
            "position_id": pos_id or "",
            "team_id": rep["team_id"],
            "name": rep["name"],
            "email": rep["email"],
            "region": rep["region"],
            "hire_date": rep["hire_date"],
            "source_system": "meridian-cloud",
            "mapping_basis": "rep-mirror",
            "evidence_score": "0.95",
            "created_at": _now(),
            "effective_start_date": rep["hire_date"],
            "effective_end_date": "",
        })
    _write("users.csv", users_canonical)
    rep_to_user: dict[str, str] = {rep["id"]: u["id"] for rep, u in zip(reps_rows, users_canonical)}
    user_id_set = {u["id"] for u in users_canonical}

    # ── 9. managers.csv (from manager_of_user.csv) ───────────────────────────
    src_managers = _read("manager_of_user.csv")
    managers_rows = []
    # Build user_id lookup: rep_uuid → canonical_user_id
    # source: user_id=rep_uuid (same UUIDs as users.csv.id), manager_user_id=rep_uuid
    for m in src_managers:
        uid_raw = str(m.get("user_id") or "").strip()
        mid_raw = str(m.get("manager_user_id") or "").strip()
        # Validate UUIDs
        try:
            uuid.UUID(uid_raw)
        except ValueError:
            uid_raw = rep_id_by_ext.get(uid_raw, "")
        try:
            uuid.UUID(mid_raw)
        except ValueError:
            mid_raw = rep_id_by_ext.get(mid_raw, "")
        if not uid_raw:
            continue
        canonical_uid = rep_to_user.get(uid_raw, "")
        canonical_mid = rep_to_user.get(mid_raw, "")
        if not canonical_uid:
            continue
        managers_rows.append({
            "id": str(uuid.uuid4()),
            "user_id": canonical_uid,
            "manager_user_id": canonical_mid,
            "source_system": "meridian-cloud",
            "created_at": _now(),
        })
    _write("managers.csv", managers_rows)

    # ── 10. plans.csv ─────────────────────────────────────────────────────────
    src_plans = _read("plans.csv")
    plans_rows = []
    plan_id_set_canonical: set[str] = set()
    for p in src_plans:
        pid_raw = str(p.get("id") or "").strip()
        try:
            pid = str(uuid.UUID(pid_raw))
        except ValueError:
            pid = str(uuid.uuid5(uuid.NAMESPACE_URL, f"meridian-plan::{pid_raw}"))
        if pid in plan_id_set_canonical:
            continue
        plan_id_set_canonical.add(pid)
        plans_rows.append({
            "id": pid,
            "external_id": str(p.get("external_id") or pid_raw),
            "name": str(p.get("name") or "Plan"),
            "description": str(p.get("description") or ""),
            "source_system": "meridian-cloud",
            "created_at": _now(),
            "effective_start_date": _safe_date(str(p.get("effective_start_date") or "")),
            "effective_end_date": _safe_date(str(p.get("effective_end_date") or "")),
        })
    _write("plans.csv", plans_rows)
    default_plan_id = plans_rows[0]["id"] if plans_rows else ""

    # ── 11. rules.csv ─────────────────────────────────────────────────────────
    src_rules = _read("rules.csv")
    rules_rows = []
    for r in src_rules:
        rid_raw = str(r.get("id") or "").strip()
        try:
            rid = str(uuid.UUID(rid_raw))
        except ValueError:
            rid = str(uuid.uuid5(uuid.NAMESPACE_URL, f"meridian-rule::{rid_raw}"))
        plan_id_raw = str(r.get("plan_id") or "").strip()
        try:
            plan_id = str(uuid.UUID(plan_id_raw))
        except ValueError:
            plan_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"meridian-plan::{plan_id_raw}"))
        if plan_id not in plan_id_set_canonical:
            plan_id = default_plan_id
        rate_raw = _safe_float(r.get("rate") or "0.05")
        rules_rows.append({
            "id": rid,
            "plan_id": plan_id,
            "name": str(r.get("name") or "Rule"),
            "metric_name": str(r.get("metric") or r.get("type") or "attainment_pct"),
            "threshold_min": "0",
            "threshold_max": "999",
            "rate": str(rate_raw),
            "accelerator_rate": "0.0",
            "bonus_amount": "0",
            "rollup_enabled": "false",
            "rollup_pct": "0.0",
            "carryover_enabled": "false",
            "carryover_cap": "0.0",
            "source_system": "meridian-cloud",
            "created_at": _now(),
        })
    _write("rules.csv", rules_rows)

    # ── 12. territories.csv ───────────────────────────────────────────────────
    src_territories = _read("territory.csv")
    territories_rows = []
    territory_id_set: set[str] = set()
    for t in src_territories:
        tid_raw = str(t.get("territory_id") or "").strip()
        try:
            tid = str(uuid.UUID(tid_raw))
        except ValueError:
            tid = str(uuid.uuid5(uuid.NAMESPACE_URL, f"meridian-territory::{tid_raw}"))
        if tid in territory_id_set:
            continue
        territory_id_set.add(tid)
        territories_rows.append({
            "id": tid,
            "external_id": tid_raw,
            "territory_code": str(t.get("territory_id") or ""),
            "name": str(t.get("territory_name") or ""),
            "parent_territory_id": "",
            "region": str(t.get("region") or ""),
            "segment": str(t.get("segment") or ""),
            "source_system": "meridian-cloud",
            "created_at": _now(),
            "effective_start_date": _safe_date(str(t.get("effective_start_date") or "")),
            "effective_end_date": _safe_date(str(t.get("effective_end_date") or "")),
        })
    _write("territories.csv", territories_rows)

    # ── 13. plan_assignments.csv ──────────────────────────────────────────────
    src_pa = _read("plan_assignment.csv")
    plan_assignments_rows = []
    for pa in src_pa:
        pa_id_raw = str(pa.get("plan_assignment_id") or "").strip()
        try:
            pa_id = str(uuid.UUID(pa_id_raw))
        except ValueError:
            pa_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"meridian-pa::{pa_id_raw}"))
        user_id_raw = str(pa.get("user_id") or "").strip()
        try:
            uuid.UUID(user_id_raw)
            canonical_uid = rep_to_user.get(user_id_raw, "")
        except ValueError:
            canonical_uid = rep_to_user.get(rep_id_by_ext.get(user_id_raw, ""), "")
        if not canonical_uid or canonical_uid not in user_id_set:
            continue
        plan_id_raw = str(pa.get("plan_id") or "").strip()
        try:
            plan_id = str(uuid.UUID(plan_id_raw))
        except ValueError:
            plan_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"meridian-plan::{plan_id_raw}"))
        if plan_id not in plan_id_set_canonical:
            plan_id = default_plan_id
        plan_assignments_rows.append({
            "id": pa_id,
            "user_id": canonical_uid,
            "plan_id": plan_id,
            "effective_start_date": _safe_date(str(pa.get("effective_start_date") or "")),
            "effective_end_date": _safe_date(str(pa.get("effective_end_date") or "")),
            "source_system": "meridian-cloud",
            "mapping_basis": "direct",
            "evidence_score": "0.95",
            "created_at": _now(),
        })
    _write("plan_assignments.csv", plan_assignments_rows)

    # ── 14. products.csv ──────────────────────────────────────────────────────
    src_products = _read("products.csv")
    products_rows = []
    product_id_set: set[str] = set()
    for p in src_products:
        pid_raw = str(p.get("id") or "").strip()
        try:
            pid = str(uuid.UUID(pid_raw))
        except ValueError:
            pid = str(uuid.uuid5(uuid.NAMESPACE_URL, f"meridian-product::{pid_raw}"))
        if pid in product_id_set:
            continue
        product_id_set.add(pid)
        products_rows.append({
            "id": pid,
            "external_id": str(p.get("external_id") or pid_raw),
            "product_sku": f"MC-SKU-{len(products_rows)+1:03d}",
            "name": str(p.get("name") or "Product"),
            "category": str(p.get("category") or ""),
            "source_system": "meridian-cloud",
            "created_at": _now(),
        })
    _write("products.csv", products_rows)

    # ── 15. rep_product_assignments.csv (minimal — link reps to products) ────
    rpa_rows = []
    if products_rows and reps_rows:
        for i, rep in enumerate(reps_rows):
            prod = products_rows[i % len(products_rows)]
            rpa_rows.append({
                "id": str(uuid.uuid4()),
                "rep_id": rep["id"],
                "product_id": prod["id"],
                "product_name": prod["name"],
                "product_sku": prod.get("product_sku", ""),
                "is_primary": "true",
                "specialization": "primary_seller",
                "effective_start_date": "",
                "effective_end_date": "",
                "source_system": "meridian-cloud",
                "created_at": _now(),
            })
    _write("rep_product_assignments.csv", rpa_rows)

    # ── 16. user_territory_assignments.csv ────────────────────────────────────
    uta_rows = []
    # Map territory_id from source users
    src_terr_id_to_canonical: dict[str, str] = {}
    for t in src_territories:
        tid_raw = str(t.get("territory_id") or "").strip()
        canonical = str(uuid.uuid5(uuid.NAMESPACE_URL, f"meridian-territory::{tid_raw}"))
        try:
            canonical = str(uuid.UUID(tid_raw))
        except ValueError:
            pass
        if canonical in territory_id_set:
            src_terr_id_to_canonical[tid_raw] = canonical

    for u, rep in zip(src_users, reps_rows):
        t_id_raw = str(u.get("territory_id") or "").strip()
        canonical_tid = src_terr_id_to_canonical.get(t_id_raw)
        if not canonical_tid and territories_rows:
            canonical_tid = territories_rows[0]["id"]
        if not canonical_tid:
            continue
        uid = rep_to_user.get(rep["id"])
        if not uid:
            continue
        uta_rows.append({
            "id": str(uuid.uuid4()),
            "user_id": uid,
            "territory_id": canonical_tid,
            "is_primary": "true",
            "source_system": "meridian-cloud",
            "mapping_basis": "direct",
            "evidence_score": "0.92",
            "created_at": _now(),
            "effective_start_date": rep["hire_date"],
            "effective_end_date": "",
        })
    _write("user_territory_assignments.csv", uta_rows)

    # ── 17. rep_ramp.csv (derive from hire_date) ──────────────────────────────
    today_ref = date.today()
    rep_ramp_rows = []
    ramp_schedule = {0: 0.25, 1: 0.35, 2: 0.50, 3: 0.65, 4: 0.75, 5: 0.85, 6: 1.00}
    for rep in reps_rows:
        hire_raw = rep.get("hire_date", "")
        try:
            hire_date = date.fromisoformat(hire_raw)
        except (ValueError, TypeError):
            hire_date = date(2022, 1, 1)
        for mo in range(12):
            m_ref = today_ref.month - mo
            y_ref = today_ref.year
            while m_ref <= 0:
                m_ref += 12
                y_ref -= 1
            period_date = date(y_ref, m_ref, 1)
            months_since = max(0, (period_date.year - hire_date.year) * 12 + (period_date.month - hire_date.month))
            rf = ramp_schedule.get(min(months_since, 6), 1.0)
            full_quota = 200000.0 / 4  # quarterly
            rep_ramp_rows.append({
                "rep_id": rep["id"],
                "period": period_date.strftime("%Y-%m"),
                "months_since_hire": str(months_since),
                "ramp_factor": str(rf),
                "quota_at_ramp": str(round(full_quota * rf, 2)),
                "full_quota": str(full_quota),
                "is_ramping": "true" if rf < 1.0 else "false",
            })
    _write("rep_ramp.csv", rep_ramp_rows)

    # ── 18. bookings.csv (from closed deals) ─────────────────────────────────
    bookings_rows = []
    for deal in deals_rows:
        if deal.get("stage") != "Closed Won":
            continue
        amount = _safe_float(deal.get("amount") or "0")
        term = 12 if amount < 500000 else 24
        booking_date = deal.get("actual_close_date") or deal.get("expected_close_date") or ""
        bookings_rows.append({
            "booking_id": str(uuid.uuid4()),
            "deal_id": deal["id"],
            "rep_id": deal["rep_id"],
            "account_id": deal["account_id"],
            "booking_date": booking_date,
            "amount": deal["amount"],
            "arr": str(round(amount / (term / 12), 2)),
            "mrr": str(round(amount / term, 2)),
            "product_sku": "",
            "contract_term_months": str(term),
            "revenue_type": "new_logo",
            "recognition_start_date": booking_date,
        })
    _write("bookings.csv", bookings_rows)

    # ── 19. churn_events.csv (minimal — ~10% of accounts) ────────────────────
    churn_rows = []
    for account in accounts_rows[:max(1, len(accounts_rows) // 10)]:
        churn_rows.append({
            "event_id": str(uuid.uuid4()),
            "account_id": account["id"],
            "period": today_ref.strftime("%Y-%m"),
            "event_type": "partial_contraction",
            "arr_change": str(round(-random.uniform(5000, 50000), 2)),
            "reason": "budget_cut",
            "detected_at": _now(),
        })
    _write("churn_events.csv", churn_rows)

    # ── 20. arr_waterfall.csv (from revenue rows) ─────────────────────────────
    arr_rows = []
    rev_by_rep_period: dict[str, dict[str, float]] = {}
    for r in revenue_rows:
        rid = r["rep_id"]
        p = r["period"]
        rev_by_rep_period.setdefault(rid, {})[p] = rev_by_rep_period.get(rid, {}).get(p, 0.0) + _safe_float(r["amount"])
    for rep in reps_rows:
        rid = rep["id"]
        periods_data = rev_by_rep_period.get(rid, {})
        running_arr = 120000.0
        for period, rev_amt in sorted(periods_data.items()):
            mrr_new = round(rev_amt / 12, 2)
            arr_rows.append({
                "rep_id": rid,
                "period": period,
                "mrr_new": str(mrr_new),
                "mrr_expansion": "0.0",
                "mrr_contraction": "0.0",
                "mrr_churn": "0.0",
                "mrr_renewal": "0.0",
                "mrr_net": str(mrr_new),
                "arr_start": str(round(running_arr, 2)),
                "arr_end": str(round(running_arr + mrr_new * 12, 2)),
            })
            running_arr = max(0.0, running_arr + mrr_new * 12)
    _write("arr_waterfall.csv", arr_rows)

    # ── 21. attainment_snapshots.csv ──────────────────────────────────────────
    snap_rows = []
    quota_by_rep_q: dict[tuple[str, str], float] = {}
    for q in quotas_rows:
        quota_by_rep_q[(q["rep_id"], q["period"])] = _safe_float(q["amount"])
    for (rep_id, period_month), rev_amt in rev_by_user_month.items():
        # find quarter
        try:
            yr, mo = period_month.split("-")
            q_label = f"{yr}-Q{(int(mo)-1)//3+1}"
        except ValueError:
            continue
        q_quota = quota_by_rep_q.get((rep_id, q_label), 0.0)
        m_quota = q_quota / 3 if q_quota > 0 else 0.0
        att_pct = round(rev_amt / m_quota * 100, 4) if m_quota > 0 else 0.0
        snap_rows.append({
            "id": str(uuid.uuid4()),
            "rep_id": rep_id,
            "period": period_month,
            "grain": "monthly",
            "revenue": str(round(rev_amt, 2)),
            "quota": str(round(m_quota, 2)),
            "attainment_pct": str(att_pct),
            "snapshot_date": date.today().isoformat(),
        })
    _write("attainment_snapshots.csv", snap_rows)

    # ── 22. leads.csv (empty canonical) ──────────────────────────────────────
    _write("leads.csv", [])

    # ── 23. opportunities.csv (mirror of deals) ───────────────────────────────
    opp_rows = []
    for deal in deals_rows:
        opp_rows.append({
            "id": str(uuid.uuid4()),
            "external_id": f"OPP-{deal['id'][:8].upper()}",
            "account_id": deal["account_id"],
            "owner_user_id": rep_to_user.get(deal["rep_id"], ""),
            "name": deal["name"],
            "stage": deal["stage"],
            "amount": deal["amount"],
            "close_date": deal["expected_close_date"],
            "source_system": "meridian-cloud",
            "created_at": deal["created_at"],
        })
    _write("opportunities.csv", opp_rows)

    # ── 24. sales_units.csv (sample from source, max 2000 rows) ──────────────
    src_su = _read("sales_units.csv")
    sampled_su = src_su[:2000]
    su_id_set: set[str] = set()
    sales_units_rows = []
    for s in sampled_su:
        sid_raw = str(s.get("id") or "").strip()
        try:
            sid = str(uuid.UUID(sid_raw))
        except ValueError:
            sid = str(uuid.uuid5(uuid.NAMESPACE_URL, f"meridian-su::{sid_raw}"))
        if sid in su_id_set:
            continue
        su_id_set.add(sid)
        # Assign to a random closed-won deal and rep
        deal = random.choice(deals_rows) if deals_rows else None
        sales_units_rows.append({
            "id": sid,
            "external_id": str(s.get("salesUnitNumber") or sid_raw[:8]),
            "opportunity_id": deal["id"] if deal else "",
            "account_id": deal["account_id"] if deal else "",
            "owner_user_id": rep_to_user.get(deal["rep_id"], "") if deal else "",
            "booked_date": deal.get("actual_close_date") or deal.get("expected_close_date") or "" if deal else "",
            "amount": deal.get("amount", "0") if deal else "0",
            "currency": "USD",
            "source_system": "meridian-cloud",
            "created_at": _now(),
        })
    _write("sales_units.csv", sales_units_rows)

    # ── 25. sales_unit_line_items.csv (sample 500 rows) ──────────────────────
    src_suli = _read("sales_unit_line_items.csv")
    sampled_suli = random.sample(src_suli, min(500, len(src_suli)))
    suli_rows = []
    for li in sampled_suli:
        lid_raw = str(li.get("id") or "").strip()
        try:
            lid = str(uuid.UUID(lid_raw))
        except ValueError:
            lid = str(uuid.uuid5(uuid.NAMESPACE_URL, f"meridian-li::{lid_raw}"))
        # Map to a random sales_unit_id
        su_row = random.choice(sales_units_rows) if sales_units_rows else None
        if not su_row:
            continue
        prod_id_raw = str(li.get("productId") or "").strip()
        if products_rows:
            prod = products_rows[0]  # simplify: use first product
            prod_id = prod["id"]
        else:
            prod_id = ""
        suli_rows.append({
            "id": lid,
            "sales_unit_id": su_row["id"],
            "product_id": prod_id,
            "quantity": str(li.get("numberOfUnits") or "1"),
            "unit_price": str(_safe_float(li.get("unitValue") or "0")),
            "net_amount": str(round(_safe_float(li.get("value") or "0") * AMOUNT_SCALE, 2)),
            "source_system": "meridian-cloud",
            "created_at": _now(),
        })
    _write("sales_unit_line_items.csv", suli_rows)

    # ── 26. sales_credits.csv (sample 1000 rows) ──────────────────────────────
    src_sc = _read("sales_credits.csv")
    sampled_sc = random.sample(src_sc, min(1000, len(src_sc)))
    sc_rows = []
    sc_id_set: set[str] = set()
    for sc in sampled_sc:
        scid_raw = str(sc.get("id") or "").strip()
        try:
            scid = str(uuid.UUID(scid_raw))
        except ValueError:
            scid = str(uuid.uuid5(uuid.NAMESPACE_URL, f"meridian-sc::{scid_raw}"))
        if scid in sc_id_set:
            continue
        sc_id_set.add(scid)
        user_ext = str(sc.get("userId") or "").strip()
        uid = rep_to_user.get(rep_id_by_ext.get(user_ext, ""), "")
        if not uid or uid not in user_id_set:
            if users_canonical:
                uid = users_canonical[0]["id"]
            else:
                continue
        su_row = random.choice(sales_units_rows) if sales_units_rows else None
        if not su_row:
            continue
        amt = round(_safe_float(sc.get("value") or "0") * AMOUNT_SCALE, 2)
        sc_rows.append({
            "id": scid,
            "sales_unit_id": su_row["id"],
            "user_id": uid,
            "credit_type": str(sc.get("name") or "Commission").lower().replace(" ", "_"),
            "credit_percent": "1.0",
            "credit_amount": str(amt),
            "source_system": "meridian-cloud",
            "created_at": str(sc.get("createdAt") or _now()),
        })
    _write("sales_credits.csv", sc_rows)

    # ── 27. payouts.csv (empty — will be seeded by _seed_payout_records) ─────
    _write("payouts.csv", [])

    # ── 28. ingestion_run_summary.json (sentinel for list_companies check) ───
    import json
    summary = {
        "run_id": str(uuid.uuid4()),
        "company_name": "meridian-cloud",
        "generated_at": _now(),
        "status": "success",
        "source": "transform_meridian_cloud.py",
        "source_dir": str(SRC),
    }
    (DST / "ingestion_run_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print("  wrote ingestion_run_summary.json")

    print(f"\nTransformation complete → {DST}")
    print("Run:  curl -X POST http://localhost:8000/ingestion/load_company -H 'Content-Type: application/json' -d '{\"company_name\": \"meridian-cloud\"}'")


if __name__ == "__main__":
    transform()
