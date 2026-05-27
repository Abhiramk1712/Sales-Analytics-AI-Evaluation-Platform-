"""Full payout audit for techo-solutions across all reps."""
import csv
from collections import defaultdict

co = "companies/techo-solutions"

# Roles that intentionally have no comp plan (not quota-carrying)
QUOTA_EXEMPT_ROLES = {"Chief Revenue Officer", "SVP Sales", "VP Sales"}

rep_hierarchy_roles: dict = {}
for r in csv.DictReader(open(f"{co}/rep_hierarchy.csv")):
    rep_hierarchy_roles[r["rep_id"]] = r.get("role", "Account Executive")

reps = {r["id"]: r for r in csv.DictReader(open(f"{co}/reps.csv"))}
users_list = list(csv.DictReader(open(f"{co}/users.csv")))
email_to_user = {r["email"]: r["id"] for r in users_list}

quotas: dict = defaultdict(dict)
for r in csv.DictReader(open(f"{co}/quotas.csv")):
    quotas[r["rep_id"]][r["period"]] = float(r["amount"])


def to_quarter(yyyymm: str) -> str:
    y, m = int(yyyymm[:4]), int(yyyymm[5:7])
    q = (m - 1) // 3 + 1
    return f"{y}-Q{q}"


rev_quarterly: dict = defaultdict(lambda: defaultdict(float))
for r in csv.DictReader(open(f"{co}/revenue.csv")):
    rev_quarterly[r["rep_id"]][to_quarter(r["period"][:7])] += float(r["amount"])

deals: dict = defaultdict(list)
for r in csv.DictReader(open(f"{co}/deals.csv")):
    deals[r["rep_id"]].append(r)

plan_assign: dict = {}
for r in csv.DictReader(open(f"{co}/plan_assignments.csv")):
    plan_assign[r["user_id"]] = r["plan_id"]

plans = {r["id"]: r for r in csv.DictReader(open(f"{co}/plans.csv"))}

rules_by_plan: dict = defaultdict(list)
for r in csv.DictReader(open(f"{co}/rules.csv")):
    rules_by_plan[r["plan_id"]].append(r)

credits_by_user: dict = defaultdict(list)
for r in csv.DictReader(open(f"{co}/sales_credits.csv")):
    credits_by_user[r["user_id"]].append(r)

payouts_csv: dict = defaultdict(dict)
for r in csv.DictReader(open(f"{co}/payouts.csv")):
    payouts_csv[r["user_id"]][r["period"]] = float(r["payout_amount"])

TARGET_PERIOD = "2026-Q1"

totals = dict(
    zero_rev=0, zero_payout=0, no_rules=0, no_deal_won=0,
    no_plan=0, total_credit=0.0, lost_commission=0.0,
)

hdr = (
    f"{'Rep Name':<28} {'Plan':<10} {'Rules':>5} {'Rev Q1':>10} "
    f"{'Quota Q1':>10} {'Attain%':>8} {'CSV Pay':>10} "
    f"{'Credits':>10} {'EstPay':>10}  Bugs"
)
print(hdr)
print("-" * 120)

for rep_id, rep in sorted(reps.items(), key=lambda x: x[1]["name"]):
    email = rep["email"]
    user_id = email_to_user.get(email, "")
    plan_id = plan_assign.get(user_id, "")
    plan_name = plans[plan_id]["external_id"] if plan_id and plan_id in plans else "NONE"
    rule_count = len(rules_by_plan.get(plan_id, []))

    rev_q1 = rev_quarterly[rep_id].get(TARGET_PERIOD, 0.0)
    quota_q1 = quotas[rep_id].get(TARGET_PERIOD, 0.0)
    attain = (rev_q1 / quota_q1 * 100) if quota_q1 > 0 else 0.0

    csv_payout = payouts_csv.get(user_id, {}).get(TARGET_PERIOD, 0.0)

    user_credits = credits_by_user.get(user_id, [])
    total_credit = sum(float(c["credit_amount"]) for c in user_credits)
    totals["total_credit"] += total_credit

    won = sum(1 for d in deals[rep_id] if d["stage"] == "Closed Won")
    lost = sum(1 for d in deals[rep_id] if d["stage"] == "Closed Lost")

    rep_role = rep_hierarchy_roles.get(rep_id, "Account Executive")
    is_exempt = rep_role in QUOTA_EXEMPT_ROLES

    bugs = []
    if is_exempt:
        bugs.append(f"EXEMPT({rep_role})")
    elif not plan_id:
        bugs.append("NO_PLAN")
    elif rule_count == 0:
        bugs.append("NO_RULES")
    if rev_q1 == 0.0 and not is_exempt:
        bugs.append("ZERO_REV")
    if won == 0 and not is_exempt:
        bugs.append("NO_WON")
    if csv_payout == 0.0 and not is_exempt and quota_q1 > 0:
        bugs.append("ZERO_PAY")

    est = total_credit * 0.03 if rule_count == 0 and total_credit > 0 and not is_exempt else 0.0
    if "NO_RULES" in bugs:
        totals["no_rules"] += 1
    if "ZERO_REV" in bugs:
        totals["zero_rev"] += 1
    if "ZERO_PAY" in bugs:
        totals["zero_payout"] += 1
    if "NO_WON" in bugs:
        totals["no_deal_won"] += 1
    if "NO_PLAN" in bugs:
        totals["no_plan"] += 1
    totals["lost_commission"] += est

    bug_str = ",".join(bugs) if bugs else "OK"
    print(
        f"{rep['name']:<28} {plan_name:<10} {rule_count:>5} {rev_q1:>10,.0f} "
        f"{quota_q1:>10,.0f} {attain:>8.1f}% {csv_payout:>10,.2f} "
        f"{total_credit:>10,.0f} {est:>10,.2f}  {bug_str}"
    )

print()
print("=== SUMMARY ===")
print(f"  Total reps:                    {len(reps)}")
print(f"  Reps with zero revenue:        {totals['zero_rev']}")
print(f"  Reps with no closed-won deals: {totals['no_deal_won']}")
print(f"  Reps with no plan rules:       {totals['no_rules']}")
print(f"  Reps with zero CSV payout:     {totals['zero_payout']}")
print(f"  Reps with no plan assigned:    {totals['no_plan']}")
print(f"  Total credits (all reps):      ${totals['total_credit']:,.2f}")
print(f"  Est. lost commission (@3%):    ${totals['lost_commission']:,.2f}")

print()
print("=== PLAN COVERAGE ===")
plan_reps: dict = defaultdict(list)
for rep_id, rep in reps.items():
    uid = email_to_user.get(rep["email"], "")
    pid = plan_assign.get(uid, "UNASSIGNED")
    pname = plans[pid]["external_id"] if pid in plans else "UNASSIGNED"
    plan_reps[f"{pname} ({pid[:8]})"].append(rep["name"])

for pkey, names in sorted(plan_reps.items()):
    pid_short = pkey.split("(")[-1].rstrip(")")
    full_pid = next((p for p in plans if p.startswith(pid_short)), "")
    rc = len(rules_by_plan.get(full_pid, []))
    print(f"  {pkey}: {len(names)} reps, rules={rc}")

print()
print("=== DEALS: Won/Lost/Open per rep ===")
for rep_id, rep in sorted(reps.items(), key=lambda x: x[1]["name"]):
    won  = sum(1 for d in deals[rep_id] if d["stage"] == "Closed Won")
    lost = sum(1 for d in deals[rep_id] if d["stage"] == "Closed Lost")
    open_ = sum(1 for d in deals[rep_id] if d["stage"] not in ("Closed Won", "Closed Lost"))
    print(f"  {rep['name']:<28} won={won}  lost={lost}  open={open_}")

print()
print("=== REVENUE.CSV: How many reps have revenue rows? ===")
reps_with_rev = set()
for r in csv.DictReader(open(f"{co}/revenue.csv")):
    reps_with_rev.add(r["rep_id"])
print(f"  Reps with at least 1 revenue row: {len(reps_with_rev)} / {len(reps)}")
print(f"  Reps with NO revenue rows: {len(reps) - len(reps_with_rev)}")
missing = [reps[rid]["name"] for rid in reps if rid not in reps_with_rev]
for name in sorted(missing):
    print(f"    - {name}")
