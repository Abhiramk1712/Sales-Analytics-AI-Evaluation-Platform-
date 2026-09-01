/**
 * PayoutsPage.jsx — Full payout management page wired to /payout/* endpoints.
 * Sprint 2.1
 */
import { useEffect, useMemo, useState } from "react";
import {
  BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid, Cell,
  LineChart, Line, Legend,
} from "recharts";
import { useFetch } from "../hooks/useFetch";
import { MetricCard, Skeleton, Card, SectionTitle, ErrorMessage, PaginationControls } from "../components/shared";
import { fmt, pct, withRefresh } from "../utils/format";

const API = import.meta.env.VITE_API_URL || "";

const TIER_COLORS = ["#B5D4F4", "#378ADD", "#1D9E75", "#185FA5"];
const ATTAINMENT_COLOR = (v) => (v >= 100 ? "#1D9E75" : v >= 80 ? "#EF9F27" : "#D85A30");
const fmtIncentive = (v) => Number(v || 0) > 0 ? fmt(v) : "—";

function TeamSummaryPane({ period, refreshKey, role, company }) {
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(10);
  const periodQuery = period ? `period=${encodeURIComponent(period)}` : "";
  const url = withRefresh(`/payout/team-summary${periodQuery ? `?${periodQuery}` : ""}`, refreshKey);
  const { data, loading, error } = useFetch(url, { role, company });

  if (loading) return <Skeleton h={320} />;
  if (error) return <ErrorMessage message={error} />;
  if (!data) return null;

  const { summary, rows } = data;
  const rowCount = rows?.length || 0;
  const totalPages = Math.max(1, Math.ceil(rowCount / Math.max(1, pageSize)));
  const safePage = Math.min(page, totalPages);
  const start = (safePage - 1) * pageSize;
  const pagedRows = (rows || []).slice(start, start + pageSize);

  const zeroPayoutCount = (rows || []).filter((r) => Number(r.total_payout || 0) <= 0).length;
  const belowQuotaCount = (rows || []).filter((r) => Number(r.attainment_pct || 0) < 100).length;

  return (
    <div>
      {/* Summary metrics */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(4,1fr)", gap: 10, marginBottom: "1.5rem" }}>
        <MetricCard label="Total Payout" value={fmt(summary.total_payout)} sub={`${summary.rep_count} reps`} color="#1D9E75" />
        <MetricCard label="Avg Attainment" value={pct(summary.avg_attainment_pct)} sub="across all reps" color={ATTAINMENT_COLOR(summary.avg_attainment_pct)} />
        <MetricCard label="At Quota" value={summary.reps_at_quota} sub="reps ≥ 100%" color="#1D9E75" />
        <MetricCard label="Below 80%" value={summary.reps_below_80} sub="need attention" color={summary.reps_below_80 > 0 ? "#D85A30" : "#1D9E75"} />
      </div>

      {/* Bar chart of attainment */}
      <Card style={{ marginBottom: "1.5rem" }}>
        <SectionTitle>Attainment by rep</SectionTitle>
        <div style={{ fontSize: 11, color: "var(--color-text-secondary)", marginBottom: 8 }}>
          {belowQuotaCount} reps are below quota this period. {zeroPayoutCount > 0 ? `${zeroPayoutCount} reps currently have zero payout due to missing quota/revenue or no eligible commission events.` : "All reps have non-zero payout for the selected period."}
        </div>
        <ResponsiveContainer width="100%" height={220}>
          <BarChart data={rows} margin={{ left: 0, right: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border-tertiary)" />
            <XAxis dataKey="rep_name" tick={{ fontSize: 10 }} tickFormatter={(v) => v.split(" ")[0]} />
            <YAxis tick={{ fontSize: 11 }} tickFormatter={(v) => `${v}%`} domain={[0, "auto"]} />
            <Tooltip formatter={(v, n) => n === "attainment_pct" ? pct(v) : fmt(v)} />
            <Bar dataKey="attainment_pct" name="Attainment %" radius={[4, 4, 0, 0]}>
              {rows.map((r) => (
                <Cell key={r.rep_id} fill={ATTAINMENT_COLOR(r.attainment_pct)} />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </Card>

      {/* Detail table */}
      <Card>
        <SectionTitle>Payout breakdown</SectionTitle>
        <div style={{ fontSize: 11, color: "var(--color-text-secondary)", marginBottom: 8 }}>
          Sorted by total payout. Use pagination to review the full team and investigate low-confidence or zero-payout rows.
        </div>
        <div style={{ overflowX: "auto" }}>
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
            <thead>
              <tr style={{ color: "var(--color-text-secondary)", fontSize: 11, textTransform: "uppercase", letterSpacing: "0.4px" }}>
                {["Rep", "Revenue", "Quota", "Attainment", "Commission", "Accelerator", "Bonus", "Total Payout", "Tier"].map((h) => (
                  <th key={h} style={{ textAlign: h === "Rep" ? "left" : "right", padding: "5px 8px", fontWeight: 400 }}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {pagedRows.map((r) => (
                <tr key={r.rep_id} style={{ borderTop: "0.5px solid var(--color-border-tertiary)" }}>
                  <td style={{ padding: "6px 8px" }}>{r.rep_name}</td>
                  <td style={{ textAlign: "right", padding: "6px 8px" }}>{fmt(r.revenue)}</td>
                  <td style={{ textAlign: "right", padding: "6px 8px" }}>{fmt(r.quota)}</td>
                  <td style={{ textAlign: "right", padding: "6px 8px", color: ATTAINMENT_COLOR(r.attainment_pct), fontWeight: 500 }}>{pct(r.attainment_pct)}</td>
                  <td style={{ textAlign: "right", padding: "6px 8px" }}>{fmt(r.commission)}</td>
                  <td style={{ textAlign: "right", padding: "6px 8px" }}>{fmtIncentive(r.accelerator)}</td>
                  <td style={{ textAlign: "right", padding: "6px 8px", color: Number(r.bonus || 0) > 0 ? "#1D9E75" : undefined }}>{fmtIncentive(r.bonus)}</td>
                  <td style={{ textAlign: "right", padding: "6px 8px", fontWeight: 600 }}>{fmt(r.total_payout)}</td>
                  <td style={{ textAlign: "right", padding: "6px 8px", fontSize: 10, color: "var(--color-text-secondary)" }}>
                    {`${(r.commission_rate * 100).toFixed(0)}%`}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <PaginationControls
          page={safePage}
          pageSize={pageSize}
          totalItems={rowCount}
          onPageChange={setPage}
          onPageSizeChange={(next) => {
            setPageSize(next);
            setPage(1);
          }}
        />
      </Card>
    </div>
  );
}

function RepStatementPane({ repId, repName, periods }) {
  const url = `/payout/statements/${repId}?periods=${periods}`;
  const { data, loading, error } = useFetch(url);

  if (loading) return <Skeleton h={200} />;
  if (error) return <ErrorMessage message={error} />;
  if (!data) return null;

  const statements = data.statements || [];
  const nonZeroAccel = statements.filter((s) => Number(s.accelerator || 0) > 0).length;
  const nonZeroBonus = statements.filter((s) => Number(s.bonus || 0) > 0).length;
  const avgAttainment = statements.length
    ? statements.reduce((sum, s) => sum + Number(s.attainment_pct || 0), 0) / statements.length
    : 0;

  return (
    <Card>
      <SectionTitle>Commission statements — {repName} (last {periods} months)</SectionTitle>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(3,1fr)", gap: 10, marginBottom: 10 }}>
        <MetricCard label="Avg Attainment" value={pct(avgAttainment)} sub="Across selected periods" color={avgAttainment >= 100 ? "#1D9E75" : avgAttainment >= 80 ? "#EF9F27" : "#D85A30"} />
        <MetricCard label="Accel Triggered" value={`${nonZeroAccel}/${statements.length}`} sub="Periods with > $0 accelerator" />
        <MetricCard label="Bonus Triggered" value={`${nonZeroBonus}/${statements.length}`} sub="Periods with > $0 bonus" />
      </div>
      {nonZeroAccel === 0 && nonZeroBonus === 0 && (
        <div style={{ fontSize: 11, color: "var(--color-text-secondary)", marginBottom: 8 }}>
          Accelerator and bonus are zero for these periods because either attainment stayed below trigger bands or qualifying win/deal thresholds were not met.
        </div>
      )}
      <div style={{ overflowX: "auto" }}>
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
          <thead>
            <tr style={{ color: "var(--color-text-secondary)", fontSize: 11, textTransform: "uppercase" }}>
              {["Period", "Revenue", "Quota", "Attainment", "Tier", "Commission", "Accelerator", "Bonus", "Incentive Notes", "Total", "Check"].map((h) => (
                <th key={h} style={{ textAlign: h === "Period" || h === "Tier" ? "left" : "right", padding: "4px 8px", fontWeight: 400 }}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {statements.map((s) => {
              const reconciled = Math.abs((Number(s.commission || 0) + Number(s.accelerator || 0) + Number(s.bonus || 0)) - Number(s.total_payout || 0)) < 0.01;
              const accel = Number(s.accelerator || 0);
              const bonus = Number(s.bonus || 0);
              const attainment = Number(s.attainment_pct || 0);
              const incentiveNote = accel > 0 || bonus > 0
                ? "Triggered"
                : attainment < 100
                  ? "Below 100% attainment"
                  : "No bonus threshold hit";
              return (
              <tr key={s.period} style={{ borderTop: "0.5px solid var(--color-border-tertiary)" }}>
                <td style={{ padding: "5px 8px", fontWeight: 500 }}>{s.period}</td>
                <td style={{ textAlign: "right", padding: "5px 8px" }}>{fmt(s.revenue)}</td>
                <td style={{ textAlign: "right", padding: "5px 8px" }}>{fmt(s.quota)}</td>
                <td style={{ textAlign: "right", padding: "5px 8px", color: ATTAINMENT_COLOR(s.attainment_pct) }}>{pct(s.attainment_pct)}</td>
                <td style={{ padding: "5px 8px", fontSize: 10, color: "var(--color-text-secondary)" }}>{s.tier_applied}</td>
                <td style={{ textAlign: "right", padding: "5px 8px" }}>{fmt(s.commission)}</td>
                <td style={{ textAlign: "right", padding: "5px 8px" }}>{fmtIncentive(s.accelerator)}</td>
                <td style={{ textAlign: "right", padding: "5px 8px", color: Number(s.bonus || 0) > 0 ? "#1D9E75" : undefined }}>{fmtIncentive(s.bonus)}</td>
                <td style={{ textAlign: "right", padding: "5px 8px", fontSize: 10, color: incentiveNote === "Triggered" ? "#1D9E75" : "var(--color-text-secondary)" }}>{incentiveNote}</td>
                <td style={{ textAlign: "right", padding: "5px 8px", fontWeight: 600 }}>{fmt(s.total_payout)}</td>
                <td style={{ textAlign: "right", padding: "5px 8px", color: reconciled ? "#1D9E75" : "#D85A30", fontWeight: 500 }}>{reconciled ? "OK" : "Check"}</td>
              </tr>
            )})}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

function QuotaFairnessPane({ period, role, company }) {
  const url = period ? `/payout/quota-fairness?period=${encodeURIComponent(period)}` : `/payout/quota-fairness`;
  const { data, loading, error } = useFetch(url, { role, company });

  if (loading) return <Skeleton h={150} />;
  if (error) return <ErrorMessage message={error} />;
  if (!data || data.gini === null) return <div style={{ color: "var(--color-text-secondary)", fontSize: 13 }}>No quota data available for fairness analysis.</div>;

  const fairnessColor = data.fairness_score >= 80 ? "#1D9E75" : data.fairness_score >= 60 ? "#EF9F27" : "#D85A30";

  return (
    <Card>
      <SectionTitle>Quota fairness analysis</SectionTitle>
      <div style={{ fontSize: 11, color: "var(--color-text-secondary)", marginBottom: 10 }}>
        {data.fairness_score >= 80
          ? "Quota allocation is broadly equitable across reps."
          : data.fairness_score >= 60
            ? "Quota distribution is moderately imbalanced; monitor outlier assignments."
            : "Quota distribution is imbalanced and may create execution risk or morale issues."}
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(3,1fr)", gap: 10, marginBottom: "1rem" }}>
        <MetricCard label="Fairness Score" value={`${data.fairness_score}%`} sub="100% = perfect equity" color={fairnessColor} />
        <MetricCard label="Gini Coefficient" value={data.gini?.toFixed(3) ?? "N/A"} sub="0 = equal, 1 = unequal" color={data.gini < 0.2 ? "#1D9E75" : data.gini < 0.4 ? "#EF9F27" : "#D85A30"} />
        <MetricCard label="Mean Quota" value={fmt(data.mean_quota)} sub={`σ = ${fmt(data.std_quota)}`} />
      </div>
      {data.outliers.length > 0 && (
        <div>
          <div style={{ fontSize: 12, fontWeight: 500, marginBottom: 8, color: "#D85A30" }}>Quota outliers (&gt;2σ from mean)</div>
          {data.outliers.map((o) => (
            <div key={o.rep_id} style={{ fontSize: 12, padding: "4px 0", borderBottom: "0.5px solid var(--color-border-tertiary)" }}>
              {o.rep_name} — {fmt(o.quota)} ({o.deviation_std > 0 ? "+" : ""}{o.deviation_std}σ)
            </div>
          ))}
        </div>
      )}
    </Card>
  );
}

export default function PayoutsPage({ refreshKey, activeCompany, userRole }) {
  const role = userRole || "executive";
  const company = activeCompany || "";
  // Build last 8 quarter labels for the period selector
  const QUARTER_OPTIONS = (() => {
    const opts = [];
    const now = new Date();
    let y = now.getFullYear();
    let q = Math.ceil((now.getMonth() + 1) / 3);
    for (let i = 0; i < 8; i++) {
      opts.push(`${y}-Q${q}`);
      q--;
      if (q < 1) { q = 4; y--; }
    }
    return opts;
  })();
  const PERIOD_OPTIONS = [
    { value: "ytd", label: "YTD" },
    { value: "all-time", label: "All time" },
    ...QUARTER_OPTIONS.map((q) => ({ value: q, label: q })),
  ];
  const defaultPeriod = QUARTER_OPTIONS[0]; // current quarter
  const [period, setPeriod] = useState(defaultPeriod);
  const [activeView, setActiveView] = useState("team"); // team | fairness
  const [repId, setRepId] = useState("");
  const [repName, setRepName] = useState("");
  const [stmtPeriods, setStmtPeriods] = useState(6);

  // Reset stale rep selection when company changes.
  useEffect(() => {
    setRepId("");
    setRepName("");
  }, [refreshKey]);

  // Fetch rep list to pick for statements
  const { data: repsData } = useFetch(withRefresh("/analytics/reps/performance", refreshKey), { role, company });
  const sortedReps = useMemo(
    () => [...(repsData || [])].sort((a, b) => Number(b.attainment_pct || 0) - Number(a.attainment_pct || 0)),
    [repsData]
  );

  useEffect(() => {
    if (activeView !== "statement") return;
    if (repId) return;
    if (!sortedReps.length) return;
    let cancelled = false;

    const pickRep = async () => {
      let best = sortedReps[0];
      for (const rep of sortedReps.slice(0, 12)) {
        try {
          const response = await fetch(`${API}/payout/statements/${rep.rep_id}?periods=${stmtPeriods}`);
          if (!response.ok) continue;
          const payload = await response.json();
          const rows = Array.isArray(payload?.statements) ? payload.statements : [];
          const hasIncentive = rows.some((s) => Number(s.accelerator || 0) > 0 || Number(s.bonus || 0) > 0);
          if (hasIncentive) {
            best = rep;
            break;
          }
        } catch {
          // Ignore transient fetch errors and keep scanning/fallback behavior.
        }
      }
      if (!cancelled) {
        setRepId(best.rep_id);
        setRepName(best.name || "");
      }
    };

    pickRep();
    return () => {
      cancelled = true;
    };
  }, [activeView, repId, sortedReps, stmtPeriods]);

  const VIEWS = [
    { id: "team", label: "Team Summary" },
    { id: "statement", label: "Rep Statement" },
    { id: "fairness", label: "Quota Fairness" },
    { id: "config", label: "Config" },
    { id: "forecast", label: "Payout Forecast" },
  ];

  return (
    <div>
      <div style={{ display: "flex", gap: 12, alignItems: "center", marginBottom: "1.5rem", flexWrap: "wrap" }}>
        <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
          {VIEWS.map((v) => (
            <button
              key={v.id}
              onClick={() => setActiveView(v.id)}
              style={{
                padding: "5px 14px",
                borderRadius: 6,
                border: "1px solid var(--color-border-tertiary)",
                background: activeView === v.id ? "var(--color-accent-primary)" : "transparent",
                color: activeView === v.id ? "#fff" : "var(--color-text-primary)",
                cursor: "pointer",
                fontSize: 12,
              }}
            >
              {v.label}
            </button>
          ))}
        </div>

        {(activeView === "team" || activeView === "fairness") && (
          <select
            value={period}
            onChange={(e) => setPeriod(e.target.value)}
            style={{ padding: "5px 10px", borderRadius: 6, border: "1px solid var(--color-border-tertiary)", fontSize: 12, background: "var(--color-background-secondary)", color: "var(--color-text-primary)" }}
          >
            {PERIOD_OPTIONS.map((opt) => <option key={opt.value} value={opt.value}>{opt.label}</option>)}
          </select>
        )}

        {activeView === "statement" && (
          <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
            <select
              value={repId}
              onChange={(e) => {
                const rep = repsData?.find((r) => r.rep_id === e.target.value);
                setRepId(e.target.value);
                setRepName(rep?.name || "");
              }}
              style={{ padding: "5px 10px", borderRadius: 6, border: "1px solid var(--color-border-tertiary)", fontSize: 12, background: "var(--color-background-secondary)", color: "var(--color-text-primary)" }}
            >
              <option value="">Select rep…</option>
              {sortedReps.map((r) => (
                <option key={r.rep_id} value={r.rep_id}>{r.name} ({pct(r.attainment_pct || 0)})</option>
              ))}
            </select>
            <select
              value={stmtPeriods}
              onChange={(e) => setStmtPeriods(Number(e.target.value))}
              style={{ padding: "5px 10px", borderRadius: 6, border: "1px solid var(--color-border-tertiary)", fontSize: 12, background: "var(--color-background-secondary)", color: "var(--color-text-primary)" }}
            >
              {[3, 6, 12].map((n) => <option key={n} value={n}>{n} months</option>)}
            </select>
            <span style={{ fontSize: 11, color: "var(--color-text-secondary)" }}>
              Auto-selects a rep with non-zero accelerator/bonus when available.
            </span>
          </div>
        )}
      </div>

      {activeView === "team" && <TeamSummaryPane period={period} refreshKey={refreshKey} role={role} company={company} />}
      {activeView === "statement" && repId && <RepStatementPane repId={repId} repName={repName} periods={stmtPeriods} />}
      {activeView === "statement" && !repId && <div style={{ fontSize: 13, color: "var(--color-text-secondary)" }}>Select a rep to view their commission statement.</div>}
      {activeView === "fairness" && <QuotaFairnessPane period={period} role={role} company={company} />}
      {activeView === "config" && <PayoutConfigPane role={role} company={company} />}
      {activeView === "forecast" && <PayoutForecastPane refreshKey={refreshKey} role={role} company={company} />}
    </div>
  );
}

function PayoutConfigPane({ role, company }) {
  const { data, loading, error } = useFetch("/payout/config", { role, company });

  if (loading) return <Skeleton h={200} />;
  if (error) return <ErrorMessage message={error} />;
  if (!data) return null;

  return (
    <Card>
      <SectionTitle>Active payout configuration</SectionTitle>
      <div style={{ fontSize: 12, marginBottom: "1rem", color: "var(--color-text-secondary)" }}>
        {data.is_default ? "Using default config" : "Custom config active (session-scoped)"}
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(2,1fr)", gap: 10, marginBottom: "1rem" }}>
        <MetricCard label="Accelerator Rate" value={pct(data.accelerator_rate * 100)} sub="on over-attainment revenue" />
        <MetricCard label="Team Bonus" value={fmt(data.team_bonus)} sub={`threshold: ${data.team_bonus_threshold_pct}% | min ${data.team_bonus_min_deals} deals`} />
      </div>
      <SectionTitle>Commission tiers</SectionTitle>
      <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
        <thead>
          <tr style={{ color: "var(--color-text-secondary)", fontSize: 11 }}>
            <th style={{ textAlign: "left", padding: "4px 8px", fontWeight: 400 }}>Attainment range</th>
            <th style={{ textAlign: "right", padding: "4px 8px", fontWeight: 400 }}>Rate</th>
          </tr>
        </thead>
        <tbody>
          {data.tiers.map((t, i) => (
            <tr key={i} style={{ borderTop: "0.5px solid var(--color-border-tertiary)" }}>
              <td style={{ padding: "5px 8px" }}>{t.min_attainment_pct}% – {t.max_attainment_pct}%</td>
              <td style={{ textAlign: "right", padding: "5px 8px", fontWeight: 500, color: TIER_COLORS[i % TIER_COLORS.length] }}>{t.rate_pct}%</td>
            </tr>
          ))}
        </tbody>
      </table>
    </Card>
  );
}

// ── Payout Forecast Pane ──────────────────────────────────────────────────

const FORECAST_COMMISSION_COLOR = "#378ADD";
const FORECAST_ACCEL_COLOR = "#1D9E75";
const FORECAST_BONUS_COLOR = "#EF9F27";
const FORECAST_ATTAIN_COLOR = "#D85A30";

function PayoutForecastPane({ refreshKey, role, company }) {
  const [forecastPeriods, setForecastPeriods] = useState(4);
  const url = withRefresh(`/payout/forecast?periods=${forecastPeriods}`, refreshKey);
  const { data, loading, error } = useFetch(url, { role, company });

  if (loading) return <Skeleton h={320} />;
  if (error) return <ErrorMessage message={error} />;
  if (!data) return null;

  // Build team-level chart data per quarter
  const teamByQuarter = data.periods.map((q) => {
    const row = {
      period: q,
      base: 0,
      accel: 0,
      bonus: 0,
      attainment: 0,
      attainmentSum: 0,
      count: 0,
      projectedRevenue: 0,
      quota: 0,
    };
    data.reps.forEach((rep) => {
      const qRow = rep.quarters.find((r) => r.period === q);
      if (qRow) {
        row.base += Number(qRow.projected_base_commission || 0);
        row.accel += Number(qRow.projected_accelerator || 0);
        row.bonus += Number(qRow.projected_bonus || 0);
        row.projectedRevenue += Number(qRow.projected_revenue || 0);
        row.quota += Number(qRow.quota || 0);
        row.attainmentSum += Number(qRow.projected_attainment_pct || 0);
        row.count += 1;
      }
    });
    row.attainment = row.quota > 0
      ? Math.round((row.projectedRevenue / row.quota) * 1000) / 10
      : (row.count > 0 ? Math.round(row.attainmentSum / row.count) : 0);
    return row;
  });

  return (
    <div>
      {/* Controls */}
      <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: "1.5rem" }}>
        <span style={{ fontSize: 12, color: "var(--color-text-secondary)" }}>Forecast quarters:</span>
        {[2, 4, 6, 8].map((n) => (
          <button
            key={n}
            onClick={() => setForecastPeriods(n)}
            style={{
              padding: "4px 12px", borderRadius: 6, fontSize: 12, cursor: "pointer",
              border: "1px solid var(--color-border-tertiary)",
              background: forecastPeriods === n ? "var(--color-accent-primary)" : "transparent",
              color: forecastPeriods === n ? "#fff" : "var(--color-text-primary)",
            }}
          >{n}Q</button>
        ))}
      </div>

      {/* Summary cards */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(3,1fr)", gap: 10, marginBottom: "1.5rem" }}>
        <MetricCard label="Team Projected Payout" value={fmt(data.team_projected_payout)} sub={`next ${forecastPeriods} quarters`} color="#1D9E75" />
        <MetricCard label="Reps Forecasted" value={data.rep_count} sub="with plan-aware rates" />
        <MetricCard label="Avg Quarterly" value={fmt(teamByQuarter.length > 0 ? teamByQuarter.reduce((s, r) => s + r.base + r.accel + r.bonus, 0) / teamByQuarter.length : 0)} sub="base + accel + bonus" />
      </div>

      {/* Stacked bar: commission breakdown per quarter */}
      <Card style={{ marginBottom: "1.5rem" }}>
        <SectionTitle>Team payout forecast — quarterly breakdown</SectionTitle>
        <ResponsiveContainer width="100%" height={240}>
          <BarChart data={teamByQuarter} margin={{ left: 10, right: 10 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border-tertiary)" />
            <XAxis dataKey="period" tick={{ fontSize: 11 }} />
            <YAxis tick={{ fontSize: 11 }} tickFormatter={(v) => `$${(v / 1000).toFixed(0)}K`} />
            <Tooltip formatter={(v) => fmt(v)} />
            <Legend wrapperStyle={{ fontSize: 11 }} />
            <Bar dataKey="base" name="Base Commission" stackId="a" fill={FORECAST_COMMISSION_COLOR} radius={[0, 0, 0, 0]} />
            <Bar dataKey="accel" name="Accelerator" stackId="a" fill={FORECAST_ACCEL_COLOR} radius={[0, 0, 0, 0]} />
            <Bar dataKey="bonus" name="Bonus" stackId="a" fill={FORECAST_BONUS_COLOR} radius={[4, 4, 0, 0]} />
          </BarChart>
        </ResponsiveContainer>
      </Card>

      {/* Attainment trend line */}
      <Card style={{ marginBottom: "1.5rem" }}>
        <SectionTitle>Projected avg attainment % by quarter</SectionTitle>
        <ResponsiveContainer width="100%" height={180}>
          <LineChart data={teamByQuarter} margin={{ left: 10, right: 10 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border-tertiary)" />
            <XAxis dataKey="period" tick={{ fontSize: 11 }} />
            <YAxis tick={{ fontSize: 11 }} tickFormatter={(v) => `${v}%`} domain={[0, "auto"]} />
            <Tooltip formatter={(v) => `${v}%`} />
            <Line type="monotone" dataKey="attainment" name="Avg Attainment" stroke={FORECAST_ATTAIN_COLOR} strokeWidth={2} dot={{ r: 4 }} />
          </LineChart>
        </ResponsiveContainer>
      </Card>

      {/* Per-rep table */}
      <Card>
        <SectionTitle>Per-rep projected payouts</SectionTitle>
        <div style={{ overflowX: "auto" }}>
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
            <thead>
              <tr style={{ color: "var(--color-text-secondary)", fontSize: 11 }}>
                <th style={{ textAlign: "left", padding: "4px 8px", fontWeight: 400 }}>Rep</th>
                <th style={{ textAlign: "left", padding: "4px 8px", fontWeight: 400 }}>Plan</th>
                {data.periods.map((q) => (
                  <th key={q} style={{ textAlign: "right", padding: "4px 8px", fontWeight: 400 }}>{q}</th>
                ))}
                <th style={{ textAlign: "right", padding: "4px 8px", fontWeight: 400 }}>Total</th>
                <th style={{ textAlign: "right", padding: "4px 8px", fontWeight: 400 }}>Confidence</th>
              </tr>
            </thead>
            <tbody>
              {data.reps.map((rep) => (
                <tr key={rep.rep_id} style={{ borderTop: "0.5px solid var(--color-border-tertiary)" }}>
                  <td style={{ padding: "5px 8px", fontWeight: 500 }}>{rep.rep_name}</td>
                  <td style={{ padding: "5px 8px", color: "var(--color-text-secondary)", fontSize: 11 }}>{rep.plan_name || "—"}</td>
                  {rep.quarters.map((q) => (
                    <td key={q.period} style={{ textAlign: "right", padding: "5px 8px" }}>{fmt(q.projected_total_payout)}</td>
                  ))}
                  <td style={{ textAlign: "right", padding: "5px 8px", fontWeight: 600 }}>{fmt(rep.total_projected_payout)}</td>
                  <td style={{ textAlign: "right", padding: "5px 8px" }}>
                    <span style={{
                      fontSize: 10, padding: "2px 6px", borderRadius: 999,
                      background: rep.forecast_confidence === "high" ? "#1D9E75" : rep.forecast_confidence === "medium" ? "#EF9F27" : "#D85A30",
                      color: "#fff", fontWeight: 600,
                    }}>{rep.forecast_confidence}</span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  );
}
