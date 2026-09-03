import { useState, useEffect, useRef, useCallback, useMemo } from "react";
import { createPortal } from "react-dom";
import { LineChart, Line, BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid, Legend, ScatterChart, Scatter, Cell } from "recharts";
import { PaginationControls } from "./components/shared";
import { useFetch } from "./hooks/useFetch";
import { setRequestContext } from "./api/client";
import { useUrlState } from "./hooks/useUrlState";

// ── New page imports (Sprint 2) ───────────────────────────────────────────
import PayoutsPage from "./pages/PayoutsPage";
import PayoutAuditPage from "./pages/PayoutAuditPage";
import ARRWaterfallPage from "./pages/ARRWaterfallPage";
import RepScorecardPage from "./pages/RepScorecardPage";
import AgentPage from "./pages/AgentPage";
import OrgHierarchyPage from "./pages/OrgHierarchyPage";
import PlansPage from "./pages/PlansPage";
import TerritoriesPage from "./pages/TerritoriesPage";
import MLInsightsPage from "./pages/MLInsightsPage";

// ── RBAC Role config ──────────────────────────────────────────────────────
const ROLES = [
  { value: "executive",    label: "Executive" },
  { value: "revops_admin", label: "RevOps Admin" },
  { value: "finance_admin", label: "Finance Admin" },
  { value: "sales_manager", label: "Sales Manager" },
  { value: "sales_rep", label: "Sales Rep" },
  { value: "data_scientist", label: "Data Scientist" },
  { value: "auditor", label: "Auditor" },
];

const PERIOD_OPTIONS = [
  "all time",
  "this month", "last month", "this quarter", "last quarter",
  "YTD", "last year",
  "Q1 2026", "Q2 2026",
  "Q1 2025", "Q2 2025", "Q3 2025", "Q4 2025",
  "Q1 2024", "Q2 2024", "Q3 2024", "Q4 2024",
];

const API = import.meta.env.VITE_API_URL || "";
const DEMO_MODE = (import.meta.env.VITE_DEMO_MODE || "true") !== "false";

const fmt  = (n) => n >= 1e6 ? `$${(n/1e6).toFixed(1)}M` : n >= 1e3 ? `$${(n/1e3).toFixed(0)}K` : `$${n}`;
const pct  = (n) => `${Number(n).toFixed(1)}%`;
const withRefresh = (url, refreshKey) => (url.includes("?") ? `${url}&_r=${refreshKey}` : `${url}?_r=${refreshKey}`);

// App used to define its own useFetch here. It read opts.role but silently
// ignored opts.company, so all 30 call sites passed a company that was never
// sent. That was invisible while the database held one company at a time —
// every request got the only tenant there was — and became a visible bug the
// moment two could be resident: the dashboard showed the default tenant's
// numbers while the selector said otherwise. The shared hook sends both
// headers; there is now one implementation rather than two.

// Helper: add period param to URL
const withPeriod = (url, period) => {
  if (!period) return url;
  const sep = url.includes("?") ? "&" : "?";
  return `${url}${sep}period=${encodeURIComponent(period)}`;
};

const STAGE_COLORS = {
  Prospecting: "#B5D4F4", Qualification: "#85B7EB", Proposal: "#378ADD",
  Negotiation: "#185FA5", "Closed Won": "#1D9E75", "Closed Lost": "#D85A30",
};
const PERSONA_COLORS = {
  "Top Performer": "#1D9E75", "High Volume": "#378ADD",
  "Rising Star": "#EF9F27", "Needs Coaching": "#D85A30",
};

// ── Metric Card ───────────────────────────────────────────────────────────
function MetricCard({ label, value, sub, color, trend, icon }) {
  const trendColor = trend === "up" ? "var(--color-green)" : trend === "down" ? "var(--color-red)" : "var(--color-text-tertiary)";
  const trendArrow = trend === "up" ? "↑" : trend === "down" ? "↓" : null;
  return (
    <div
      style={{
        background: "var(--color-background-primary)",
        borderRadius: "var(--border-radius-lg)",
        border: "1px solid var(--color-border-secondary)",
        padding: "16px 18px",
        minHeight: 100,
        boxShadow: "var(--shadow-sm)",
        display: "flex",
        flexDirection: "column",
        justifyContent: "space-between",
        transition: "box-shadow var(--transition-fast), transform var(--transition-fast)",
      }}
      onMouseEnter={e => { e.currentTarget.style.boxShadow = "var(--shadow-md)"; e.currentTarget.style.transform = "translateY(-1px)"; }}
      onMouseLeave={e => { e.currentTarget.style.boxShadow = "var(--shadow-sm)"; e.currentTarget.style.transform = "translateY(0)"; }}
    >
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 10 }}>
        <div style={{ fontSize: 11, fontWeight: 600, color: "var(--color-text-secondary)", textTransform: "uppercase", letterSpacing: "0.6px" }}>{label}</div>
        {icon && <span style={{ fontSize: 15, opacity: 0.75 }}>{icon}</span>}
      </div>
      <div style={{ display: "flex", alignItems: "baseline", gap: 6 }}>
        <div style={{ fontSize: 24, fontWeight: 700, color: color || "var(--color-text-primary)", letterSpacing: "-0.5px", lineHeight: 1 }}>{value}</div>
        {trendArrow && <span style={{ fontSize: 12, fontWeight: 600, color: trendColor }}>{trendArrow}</span>}
      </div>
      {sub && <div style={{ fontSize: 11, marginTop: 8, color: "var(--color-text-tertiary)", fontWeight: 500 }}>{sub}</div>}
    </div>
  );
}

// ── Loading Skeleton ──────────────────────────────────────────────────────
function Skeleton({ h = 200 }) {
  return <div className="skeleton-shimmer" style={{ borderRadius: "var(--border-radius-md)", height: h }} />;
}

// ── Dashboard Tab ─────────────────────────────────────────────────────────
function DashboardTab({ refreshKey, period, userRole, activeCompany }) {
  const role = userRole || "executive";
  const company = activeCompany || "";
  const kpisUrl = withPeriod(withRefresh("/analytics/kpis", refreshKey), period);
  const { data: kpis, loading: kLoading }   = useFetch(kpisUrl, { role, company });
  // D1: wire period to revenue/monthly chart
  const monthlyUrl = withPeriod(withRefresh("/analytics/revenue/monthly?months=12", refreshKey), period);
  const { data: monthly, loading: mLoading } = useFetch(monthlyUrl, { role, company });
  const repsUrl = withPeriod(withRefresh("/analytics/reps/performance", refreshKey), period);
  const { data: stages, loading: sLoading }  = useFetch(withRefresh("/analytics/pipeline/stages", refreshKey), { role, company });
  const { data: reps, loading: rLoading }    = useFetch(repsUrl, { role, company });
  // D10: Drivers panel
  const driversUrl = withPeriod(withRefresh("/analytics/drivers", refreshKey), period);
  const { data: drivers } = useFetch(driversUrl, { role, company });

  const topPerformers = reps ? [...reps].sort((a, b) => b.attainment_pct - a.attainment_pct).slice(0, 5) : [];

  return (
    <div>
      {kLoading ? <Skeleton h={90} /> : (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(4,1fr)", gap: 12, marginBottom: "1.5rem" }}>
          <MetricCard label="Revenue" value={fmt(kpis.total_revenue)} sub={`${pct(kpis.attainment_pct)} of quota`} color={kpis.attainment_pct >= 100 ? "var(--color-green)" : undefined} icon="💰" trend={kpis.attainment_pct >= 100 ? "up" : "down"} />
          <MetricCard label="Open Pipeline" value={fmt(kpis.open_pipeline)} sub={`${kpis.open_deal_count} deals`} icon="📊" />
          <MetricCard label="Win Rate" value={pct(kpis.win_rate)} sub={`${kpis.deals_won} won / ${kpis.deals_lost} lost`} color={kpis.win_rate >= 50 ? "var(--color-green)" : "var(--color-red)"} icon="🎯" trend={kpis.win_rate >= 50 ? "up" : "down"} />
          <MetricCard label="Quota Attainment" value={pct(kpis.attainment_pct)} sub={`${fmt(kpis.total_quota)} total quota`} color={kpis.attainment_pct >= 100 ? "var(--color-green)" : kpis.attainment_pct >= 80 ? "var(--color-amber)" : "var(--color-red)"} icon="🏆" />
        </div>
      )}

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16, marginBottom: "1.5rem" }}>
        <div style={{ border: "1px solid var(--color-border-secondary)", borderRadius: "var(--border-radius-lg)", padding: 20, background: "var(--color-background-primary)", boxShadow: "var(--shadow-sm)" }}>
          <div style={{ fontSize: 13, fontWeight: 700, marginBottom: 4, color: "var(--color-text-primary)" }}>Monthly Revenue</div>
          <div style={{ fontSize: 11, color: "var(--color-text-tertiary)", marginBottom: 14 }}>12-month trend</div>
          {mLoading ? <Skeleton /> : (
            <ResponsiveContainer width="100%" height={200}>
              <LineChart data={monthly}>
                <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border-tertiary)" />
                <XAxis dataKey="period" tick={{ fontSize: 11 }} tickFormatter={v => v.slice(5)} />
                <YAxis tick={{ fontSize: 11 }} tickFormatter={v => fmt(v)} width={70} />
                <Tooltip formatter={v => [fmt(v), "Revenue"]} contentStyle={{ borderRadius: 8, border: "1px solid var(--color-border-secondary)", boxShadow: "var(--shadow-md)", fontSize: 12 }} />
                <Line type="monotone" dataKey="revenue" stroke="var(--color-blue)" strokeWidth={2.5} dot={false} activeDot={{ r: 4 }} />
              </LineChart>
            </ResponsiveContainer>
          )}
        </div>

        <div style={{ border: "1px solid var(--color-border-secondary)", borderRadius: "var(--border-radius-lg)", padding: 20, background: "var(--color-background-primary)", boxShadow: "var(--shadow-sm)" }}>
          <div style={{ fontSize: 13, fontWeight: 700, marginBottom: 4, color: "var(--color-text-primary)" }}>Pipeline by Stage</div>
          <div style={{ fontSize: 11, color: "var(--color-text-tertiary)", marginBottom: 14 }}>Current open deals value</div>
          {sLoading ? <Skeleton /> : (
            <ResponsiveContainer width="100%" height={200}>
              <BarChart data={stages} layout="vertical">
                <XAxis type="number" tick={{ fontSize: 11 }} tickFormatter={v => fmt(v)} />
                <YAxis dataKey="stage" type="category" tick={{ fontSize: 11 }} width={90} />
                <Tooltip formatter={v => [fmt(v), "Value"]} contentStyle={{ borderRadius: 8, border: "1px solid var(--color-border-secondary)", boxShadow: "var(--shadow-md)", fontSize: 12 }} />
                <Bar dataKey="value" radius={[0, 5, 5, 0]}>
                  {stages?.map((s) => <Cell key={s.stage} fill={STAGE_COLORS[s.stage] || "#888"} />)}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          )}
        </div>
      </div>

      {/* Top Performers */}
      <div style={{ border: "1px solid var(--color-border-secondary)", borderRadius: "var(--border-radius-lg)", padding: 20, marginBottom: "1.5rem", background: "var(--color-background-primary)", boxShadow: "var(--shadow-sm)" }}>
        <div style={{ fontSize: 13, fontWeight: 700, marginBottom: 4 }}>Top Performers</div>
        <div style={{ fontSize: 11, color: "var(--color-text-tertiary)", marginBottom: 14 }}>Ranked by quota attainment</div>
        {rLoading ? <Skeleton h={120} /> : topPerformers.length === 0 ? (
          <div style={{ fontSize: 12, color: "var(--color-text-secondary)" }}>No rep data available.</div>
        ) : (
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
              <thead>
                <tr style={{ background: "var(--color-background-secondary)" }}>
                  {["Rank", "Rep", "Revenue", "Quota", "Attainment", "Win Rate", "Open Pipeline"].map(h => (
                    <th key={h} style={{ textAlign: h === "Rank" || h === "Rep" ? "left" : "right", padding: "9px 12px", fontSize: 11, fontWeight: 600, color: "var(--color-text-secondary)", textTransform: "uppercase", letterSpacing: "0.5px", borderBottom: "1px solid var(--color-border-secondary)" }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {topPerformers.map((rep, i) => {
                  const attColor = rep.attainment_pct >= 100 ? "var(--color-green)" : rep.attainment_pct >= 75 ? "var(--color-amber)" : "var(--color-red)";
                  const medals = ["🥇", "🥈", "🥉"];
                  return (
                    <tr key={rep.rep_id} style={{ borderTop: "1px solid var(--color-border-tertiary)", transition: "background var(--transition-fast)" }}
                      onMouseEnter={e => e.currentTarget.style.background = "var(--color-background-secondary)"}
                      onMouseLeave={e => e.currentTarget.style.background = "transparent"}>
                      <td style={{ padding: "10px 12px", fontSize: 14 }}>{medals[i] || `#${i + 1}`}</td>
                      <td style={{ padding: "10px 12px" }}>
                        <div style={{ fontWeight: 600 }}>{rep.name}</div>
                        <div style={{ fontSize: 11, color: "var(--color-text-tertiary)" }}>{rep.region || "—"}</div>
                      </td>
                      <td style={{ padding: "10px 12px", textAlign: "right", fontWeight: 600 }}>{fmt(rep.revenue)}</td>
                      <td style={{ padding: "10px 12px", textAlign: "right", color: "var(--color-text-secondary)" }}>{fmt(rep.quota)}</td>
                      <td style={{ padding: "10px 12px", textAlign: "right" }}>
                        <span style={{ fontWeight: 700, color: attColor }}>{pct(rep.attainment_pct)}</span>
                      </td>
                      <td style={{ padding: "10px 12px", textAlign: "right", color: rep.win_rate >= 50 ? "var(--color-green)" : "var(--color-text-secondary)" }}>{pct(rep.win_rate)}</td>
                      <td style={{ padding: "10px 12px", textAlign: "right" }}>{fmt(rep.open_pipeline)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
        )}
      </div>

      {/* D10: Period-over-period Drivers panel */}
      {drivers && (drivers.drivers || []).length > 0 && (
        <div style={{ border: "1px solid var(--color-border-secondary)", borderRadius: "var(--border-radius-lg)", padding: 20, background: "var(--color-background-primary)", boxShadow: "var(--shadow-sm)" }}>
          <div style={{ fontSize: 13, fontWeight: 700, marginBottom: 4 }}>Period-over-period Drivers</div>
          <div style={{ fontSize: 11, color: "var(--color-text-tertiary)", marginBottom: 14 }}>Key revenue movement factors</div>
          <div style={{ display: "grid", gap: 8 }}>
            {(drivers.drivers || []).slice(0, 5).map((d, i) => {
              const isPos = (d.delta ?? 0) >= 0;
              return (
                <div key={i} style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "10px 14px", borderRadius: "var(--border-radius-md)", background: isPos ? "var(--color-green-light)" : "var(--color-red-light)", border: `1px solid ${isPos ? "#86efac" : "#fca5a5"}` }}>
                  <span style={{ fontSize: 12, fontWeight: 600 }}>{d.metric || d.driver}</span>
                  <span style={{ fontSize: 12, fontWeight: 700, color: isPos ? "var(--color-green)" : "var(--color-red)" }}>
                    {isPos ? "+" : ""}{typeof d.delta_pct !== "undefined" ? pct(d.delta_pct) : (d.delta != null ? fmt(Math.abs(d.delta)) : "—")}
                  </span>
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}

// ── Forecast Tab ──────────────────────────────────────────────────────────

/** Return a plain-English business verdict for a forecast accuracy metric. */
function forecastContext(metric, value) {
  if (value == null || isNaN(value)) return "no data";
  if (metric === "MAE") {
    // Dollar amount — compare to order-of-magnitude of typical monthly revenue
    if (value < 5000)   return "excellent — avg miss under $5 K/month";
    if (value < 20000)  return "good — avg miss under $20 K/month";
    if (value < 50000)  return "moderate — revisit model inputs";
    return "high — forecast reliability limited";
  }
  if (metric === "RMSE") {
    // RMSE > MAE signals large outlier errors
    if (value < 10000)  return "low variance in errors — stable model";
    if (value < 40000)  return "some large misses; check outlier months";
    if (value < 100000) return "notable outlier errors — review seasonality";
    return "severe outliers detected — model needs retraining";
  }
  if (metric === "MAPE") {
    // Percentage
    if (value < 5)   return "< 5% off — highly reliable for planning";
    if (value < 10)  return "< 10% off — acceptable for budgeting";
    if (value < 20)  return "10–20% off — use with caution in board decks";
    return "> 20% off — not suitable for revenue commitments";
  }
  return "";
}

function ForecastTab({ refreshKey, activeCompany, period, userRole }) {
  const role = userRole || "executive";
  const company = activeCompany || "";
  const companyParam = activeCompany ? `&company=${encodeURIComponent(activeCompany)}` : "";
  const { data, loading, error } = useFetch(withRefresh(`/ml/forecast/revenue?horizon=6${companyParam}`, refreshKey), { role, company });
  const { data: labData, loading: labLoading, error: labError } = useFetch(
    withRefresh(`/ml/forecast/lab?forecast_type=revenue&horizon=6&include_multi_scenario=true${companyParam}`, refreshKey),
    { role, company }
  );
  const historical = data?.historical || {};
  const forecastPeriods = data?.forecast_periods || [];
  const forecastValues = data?.forecast_values || [];
  const lowerCi = data?.lower_ci || [];
  const upperCi = data?.upper_ci || [];
  const commitLane   = data?.commit_lane    || [];  // D3
  const bestCaseLane = data?.best_case_lane || [];  // D3
  const modelMetrics = data?.model_metrics || {};
  const ensembleWeights = data?.ensemble_weights || {};
  const forecastMode = data?.metadata?.forecast_mode || "model";
  const historyMonths = data?.metadata?.history_months ?? Object.keys(historical).length;

  const labScenarios = labData?.scenario_matrix || {};
  const labBase = labScenarios.base || null;
  const labOptimistic = labScenarios.optimistic || null;
  const labConservative = labScenarios.conservative || null;
  const labPeriods = labBase?.periods || [];
  const labMapePct = labData?.backtest?.mape != null ? Number(labData.backtest.mape) * 100 : null;
  const spreadIndex = labPeriods.length > 0 ? labPeriods.length - 1 : -1;
  const scenarioSpread = spreadIndex >= 0
    ? Math.max(0, Number(labOptimistic?.values?.[spreadIndex] || 0) - Number(labConservative?.values?.[spreadIndex] || 0))
    : null;

  const nextForecast = forecastValues.length > 0 ? forecastValues[0] : null;
  const avgForecast = forecastValues.length > 0
    ? forecastValues.reduce((sum, v) => sum + Number(v || 0), 0) / forecastValues.length
    : null;

  const forecastDeltaPct = forecastValues.length >= 2 && Number(forecastValues[0] || 0) !== 0
    ? ((Number(forecastValues[forecastValues.length - 1] || 0) - Number(forecastValues[0] || 0)) / Number(forecastValues[0] || 1)) * 100
    : null;
  const forecastDirection = forecastDeltaPct == null ? "flat" : forecastDeltaPct > 2 ? "up" : forecastDeltaPct < -2 ? "down" : "flat";
  const forecastSuggestions = [
    forecastDirection === "up"
      ? "Prepare delivery and onboarding capacity for growth periods to protect conversion-to-cash timelines."
      : forecastDirection === "down"
        ? "Increase top-of-funnel generation and run deal inspection on late-stage opportunities to protect near-term bookings."
        : "Run scenario stress tests on pricing and conversion assumptions before setting hard commitments.",
    scenarioSpread != null && scenarioSpread > 0
      ? `Scenario spread is ${fmt(scenarioSpread)} by period end; use conservative numbers for committed targets and optimistic for stretch planning.`
      : "Scenario spread is narrow; base projection is relatively stable.",
    labMapePct != null && labMapePct > 10
      ? "Backtest error is elevated; supplement forecast with weekly pipeline quality checks."
      : "Backtest error is within acceptable range for operating-plan guidance.",
  ];

  const chartData = data ? [
    ...Object.entries(historical).slice(-12).map(([p, v]) => ({ period: p, revenue: v, type: "historical" })),
    ...forecastPeriods.map((p, i) => ({ period: p, forecast: forecastValues[i], lower: lowerCi[i], upper: upperCi[i], commit: commitLane[i], best_case: bestCaseLane[i], type: "forecast" })),
  ] : [];

  return (
    <div>
      {loading ? <Skeleton h={300} /> : error ? (
        <div style={{ border: "0.5px solid var(--color-border-tertiary)", borderRadius: "var(--border-radius-lg)", padding: 16, color: "#D85A30" }}>
          Forecast unavailable: {error}
        </div>
      ) : !data ? (
        <div style={{ border: "0.5px solid var(--color-border-tertiary)", borderRadius: "var(--border-radius-lg)", padding: 16 }}>No forecast data available.</div>
      ) : (
        <>
          <div style={{ fontSize: 12, color: "var(--color-text-secondary)", marginBottom: 10 }}>
            Showing forecast for selected company: <strong>{activeCompany || "N/A"}</strong>
          </div>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(3,1fr)", gap: 10, marginBottom: "1.5rem" }}>
            {forecastMode === "model" ? (
              <>
                <MetricCard label="MAE" value={modelMetrics.MAE != null ? fmt(modelMetrics.MAE) : "n/a"} sub={forecastContext("MAE", modelMetrics.MAE)} color={modelMetrics.MAE == null ? undefined : modelMetrics.MAE < 20000 ? "#1D9E75" : modelMetrics.MAE < 50000 ? "#EF9F27" : "#D85A30"} />
                <MetricCard label="RMSE" value={modelMetrics.RMSE != null ? fmt(modelMetrics.RMSE) : "n/a"} sub={forecastContext("RMSE", modelMetrics.RMSE)} color={modelMetrics.RMSE == null ? undefined : modelMetrics.RMSE < 40000 ? "#1D9E75" : modelMetrics.RMSE < 100000 ? "#EF9F27" : "#D85A30"} />
                <MetricCard label="MAPE" value={modelMetrics.MAPE != null ? pct(modelMetrics.MAPE) : "n/a"} sub={forecastContext("MAPE", modelMetrics.MAPE)} color={modelMetrics.MAPE == null ? undefined : modelMetrics.MAPE < 10 ? "#1D9E75" : modelMetrics.MAPE < 20 ? "#EF9F27" : "#D85A30"} />
              </>
            ) : (
              <>
                <MetricCard label="Forecast Mode" value={forecastMode} sub={`${historyMonths} months history`} />
                <MetricCard label="Next Month" value={nextForecast != null ? fmt(nextForecast) : "n/a"} sub={forecastPeriods[0] || "n/a"} />
                <MetricCard label="6-Month Avg" value={avgForecast != null ? fmt(avgForecast) : "n/a"} sub="Average forecast value" />
              </>
            )}
          </div>

          <div style={{ border: "0.5px solid var(--color-border-tertiary)", borderRadius: "var(--border-radius-lg)", padding: 12, marginBottom: 16, background: "var(--color-background-secondary)" }}>
            <div style={{ fontSize: 13, fontWeight: 500, marginBottom: 6 }}>Business Context & Next Best Actions</div>
            <div style={{ fontSize: 12, color: "var(--color-text-secondary)", marginBottom: 8 }}>
              Forecast trend is <strong>{forecastDirection}</strong>{forecastDeltaPct != null ? ` (${pct(forecastDeltaPct)}) over the horizon` : ""}. Translate this into staffing, quota pacing, and risk controls.
            </div>
            <div style={{ display: "grid", gap: 6, fontSize: 12, color: "var(--color-text-secondary)" }}>
              {forecastSuggestions.map((item) => (
                <div key={item}>• {item}</div>
              ))}
            </div>
          </div>

          <div style={{ border: "0.5px solid var(--color-border-tertiary)", borderRadius: "var(--border-radius-lg)", padding: 16, marginBottom: 16 }}>
            <div style={{ fontSize: 13, fontWeight: 500, marginBottom: 4 }}>Revenue forecast — next 6 months</div>
            <div style={{ fontSize: 12, color: "var(--color-text-secondary)", marginBottom: 14 }}>Model: {data.model_info || "n/a"} · SARIMAX: {(((ensembleWeights.sarimax || 0) * 100).toFixed(0))}% · Ridge: {(((ensembleWeights.ridge || 0) * 100).toFixed(0))}% · GBR: {(((ensembleWeights.gbr || 0) * 100).toFixed(0))}%</div>
            {(data.warnings || []).length > 0 && (
              <div style={{ fontSize: 12, color: forecastMode === "baseline" ? "#EF9F27" : "#1D9E75", marginBottom: 10, padding: "8px 12px", background: forecastMode === "baseline" ? "rgba(239,159,39,0.08)" : "rgba(29,158,117,0.08)", borderRadius: 6, borderLeft: `3px solid ${forecastMode === "baseline" ? "#EF9F27" : "#1D9E75"}` }}>
                {forecastMode === "baseline"
                  ? "⚠ Baseline forecast — only 12 months of history available. Forecast carries forward the last known value. Train with 36+ months of data for trend-based predictions."
                  : (data.warnings || []).join(" | ")}
              </div>
            )}
            <ResponsiveContainer width="100%" height={260}>
              <LineChart data={chartData}>
                <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border-tertiary)" />
                <XAxis dataKey="period" tick={{ fontSize: 10 }} tickFormatter={v => v.slice(5)} />
                <YAxis tick={{ fontSize: 11 }} tickFormatter={v => fmt(v)} />
                <Tooltip formatter={v => fmt(v)} />
                <Legend />
                <Line dataKey="revenue" stroke="#378ADD" strokeWidth={2} dot={false} name="Actual" connectNulls />
                <Line dataKey="forecast" stroke="#1D9E75" strokeWidth={2} strokeDasharray="6 3" dot={{ r: 4 }} name="Base" connectNulls />
                <Line dataKey="commit" stroke="#185FA5" strokeWidth={1.5} strokeDasharray="4 2" dot={false} name="Commit (p20)" connectNulls />
                <Line dataKey="best_case" stroke="#EF9F27" strokeWidth={1.5} strokeDasharray="4 2" dot={false} name="Best Case (p80)" connectNulls />
                <Line dataKey="upper" stroke="#B5D4F4" strokeWidth={1} strokeDasharray="3 3" dot={false} name="Upper CI (p90)" connectNulls />
                <Line dataKey="lower" stroke="#B5D4F4" strokeWidth={1} strokeDasharray="3 3" dot={false} name="Lower CI (p10)" connectNulls />
              </LineChart>
            </ResponsiveContainer>
          </div>

          <div style={{ border: "0.5px solid var(--color-border-tertiary)", borderRadius: "var(--border-radius-lg)", padding: 16, marginBottom: 16 }}>
            <div style={{ fontSize: 13, fontWeight: 500, marginBottom: 4 }}>Forecast lab — scenario matrix</div>
            <div style={{ fontSize: 12, color: "var(--color-text-secondary)", marginBottom: 10 }}>
              Strategy diagnostics from <strong>/ml/forecast/lab</strong> with base, optimistic, and conservative projections.
            </div>

            {labLoading ? (
              <Skeleton h={140} />
            ) : labError ? (
              <div style={{ fontSize: 12, color: "#D85A30" }}>Forecast lab unavailable: {labError}</div>
            ) : !labData ? (
              <div style={{ fontSize: 12, color: "var(--color-text-secondary)" }}>No forecast lab data available.</div>
            ) : (
              <>
                <div style={{ display: "grid", gridTemplateColumns: "repeat(3,1fr)", gap: 10, marginBottom: 12 }}>
                  <MetricCard
                    label="Lab Strategy"
                    value={labData.strategy_used || "n/a"}
                    sub={`source: ${labData.generated_from?.history_source || "unknown"}`}
                  />
                  <MetricCard
                    label="Backtest MAPE"
                    value={labMapePct != null ? pct(labMapePct) : "n/a"}
                    sub={labMapePct != null ? forecastContext("MAPE", labMapePct) : "No backtest metrics"}
                    color={labMapePct == null ? undefined : labMapePct < 10 ? "#1D9E75" : labMapePct < 20 ? "#EF9F27" : "#D85A30"}
                  />
                  <MetricCard
                    label="Scenario Spread"
                    value={scenarioSpread != null ? fmt(scenarioSpread) : "n/a"}
                    sub={spreadIndex >= 0 ? `optimistic - conservative in ${labPeriods[spreadIndex]}` : "No forecast periods"}
                  />
                </div>

                <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
                    <thead>
                      <tr>
                        {["Period", "Conservative", "Base", "Optimistic"].map((h) => (
                          <th
                            key={h}
                            style={{
                              textAlign: "left",
                              fontWeight: 500,
                              fontSize: 11,
                              color: "var(--color-text-secondary)",
                              textTransform: "uppercase",
                              letterSpacing: "0.5px",
                              padding: "8px 12px",
                              borderBottom: "0.5px solid var(--color-border-tertiary)",
                            }}
                          >
                            {h}
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {labPeriods.map((p, i) => (
                        <tr key={p}>
                          <td style={{ padding: "9px 12px", borderBottom: "0.5px solid var(--color-border-tertiary)" }}>{p}</td>
                          <td style={{ padding: "9px 12px", color: "#D85A30", borderBottom: "0.5px solid var(--color-border-tertiary)" }}>
                            {labConservative?.values?.[i] != null ? fmt(labConservative.values[i]) : "n/a"}
                          </td>
                          <td style={{ padding: "9px 12px", fontWeight: 500, borderBottom: "0.5px solid var(--color-border-tertiary)" }}>
                            {labBase?.values?.[i] != null ? fmt(labBase.values[i]) : "n/a"}
                          </td>
                          <td style={{ padding: "9px 12px", color: "#1D9E75", borderBottom: "0.5px solid var(--color-border-tertiary)" }}>
                            {labOptimistic?.values?.[i] != null ? fmt(labOptimistic.values[i]) : "n/a"}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
              </>
            )}
          </div>

          <div style={{ border: "0.5px solid var(--color-border-tertiary)", borderRadius: "var(--border-radius-lg)", padding: 16 }}>
            <div style={{ fontSize: 13, fontWeight: 500, marginBottom: 12 }}>Forecast table</div>
            {forecastPeriods.length === 0 && (
              <div style={{ fontSize: 12, color: "var(--color-text-secondary)", marginBottom: 10 }}>
                No forecast periods available for the selected company.
              </div>
            )}
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
                <thead><tr>{["Period","Commit (p20)","Base","Best Case (p80)","Lower CI","Upper CI"].map(h => <th key={h} style={{ textAlign: "left", fontWeight: 500, fontSize: 11, color: "var(--color-text-secondary)", textTransform: "uppercase", letterSpacing: "0.5px", padding: "8px 12px", borderBottom: "0.5px solid var(--color-border-tertiary)" }}>{h}</th>)}</tr></thead>
                <tbody>{forecastPeriods.map((p, i) => (
                  <tr key={p}><td style={{ padding: "9px 12px", borderBottom: "0.5px solid var(--color-border-tertiary)" }}>{p}</td><td style={{ padding: "9px 12px", color: "#185FA5", borderBottom: "0.5px solid var(--color-border-tertiary)" }}>{commitLane[i] != null ? fmt(commitLane[i]) : "n/a"}</td><td style={{ padding: "9px 12px", fontWeight: 500, borderBottom: "0.5px solid var(--color-border-tertiary)" }}>{forecastValues[i] != null ? fmt(forecastValues[i]) : "n/a"}</td><td style={{ padding: "9px 12px", color: "#EF9F27", borderBottom: "0.5px solid var(--color-border-tertiary)" }}>{bestCaseLane[i] != null ? fmt(bestCaseLane[i]) : "n/a"}</td><td style={{ padding: "9px 12px", color: "var(--color-text-secondary)", borderBottom: "0.5px solid var(--color-border-tertiary)" }}>{lowerCi[i] != null ? fmt(lowerCi[i]) : "n/a"}</td><td style={{ padding: "9px 12px", color: "var(--color-text-secondary)", borderBottom: "0.5px solid var(--color-border-tertiary)" }}>{upperCi[i] != null ? fmt(upperCi[i]) : "n/a"}</td></tr>
                ))}</tbody>
              </table>
          </div>
        </>
      )}
    </div>
  );
}

// ── Rep Profile Panel ─────────────────────────────────────────────────────
// ── Plan Detail Modal ────────────────────────────────────────────────────
function ProductDetailModal({ product, performance, onClose }) {
  const data = performance || {};
  const stageData = [
    { stage: "Won", count: Number(data.deals_won || 0), fill: "#1D9E75" },
    { stage: "Open", count: Number(data.deals_open || 0), fill: "#378ADD" },
    { stage: "Lost", count: Number(data.deals_lost || 0), fill: "#D85A30" },
  ];

  return createPortal(
    <div
      style={{
        position: "fixed",
        inset: 0,
        background: "rgba(0,0,0,0.45)",
        zIndex: 9999,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
      }}
      onClick={onClose}
    >
      <div
        style={{
          background: "var(--color-background-primary)",
          borderRadius: "var(--border-radius-lg)",
          border: "0.5px solid var(--color-border-secondary)",
          boxShadow: "0 24px 64px rgba(0,0,0,0.22)",
          width: 520,
          maxHeight: "85vh",
          overflow: "hidden",
          display: "flex",
          flexDirection: "column",
        }}
        onClick={(e) => e.stopPropagation()}
      >
        <div style={{ padding: "16px 20px", borderBottom: "0.5px solid var(--color-border-tertiary)", display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
          <div>
            <div style={{ fontSize: 15, fontWeight: 600 }}>{product?.name || "Product"}</div>
            <div style={{ fontSize: 12, color: "var(--color-text-secondary)", marginTop: 2 }}>
              {product?.sku || "No SKU"} {product?.specialization ? `· ${product.specialization}` : ""}
            </div>
          </div>
          <button onClick={onClose} style={{ background: "none", border: "none", cursor: "pointer", fontSize: 18, color: "var(--color-text-secondary)", lineHeight: 1, padding: "0 4px" }}>×</button>
        </div>

        <div style={{ padding: 20, display: "grid", gap: 12, overflowY: "auto" }}>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(4,1fr)", gap: 8 }}>
            <MetricCard label="Revenue" value={fmt(Number(data.revenue || 0))} />
            <MetricCard label="Open Pipeline" value={fmt(Number(data.open_pipeline || 0))} />
            <MetricCard label="Win Rate" value={pct(Number(data.win_rate || 0))} color={Number(data.win_rate || 0) >= 40 ? "#1D9E75" : "#D85A30"} />
            <MetricCard label="Mix" value={pct(Number(data.product_mix_pct || 0))} sub="Of rep closed revenue" />
          </div>

          <div style={{ border: "0.5px solid var(--color-border-tertiary)", borderRadius: "var(--border-radius-md)", padding: 12 }}>
            <div style={{ fontSize: 13, fontWeight: 500, marginBottom: 8 }}>Deal Stage Distribution</div>
            <ResponsiveContainer width="100%" height={150}>
              <BarChart data={stageData}>
                <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border-tertiary)" />
                <XAxis dataKey="stage" tick={{ fontSize: 10 }} />
                <YAxis tick={{ fontSize: 10 }} allowDecimals={false} />
                <Tooltip formatter={(v) => [String(v), "Deals"]} />
                <Bar dataKey="count" radius={[4, 4, 0, 0]}>
                  {stageData.map((row) => (
                    <Cell key={row.stage} fill={row.fill} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>

          <div style={{ fontSize: 12, color: "var(--color-text-secondary)", lineHeight: 1.6 }}>
            {Number(data.deals_total || 0) === 0
              ? "No opportunities are currently tied to this product for the selected rep."
              : Number(data.win_rate || 0) >= 50
                ? "This product is converting well for the rep. Consider expanding pipeline contribution and upsell plays."
                : "This product has lower conversion quality. Review qualification criteria and stage progression discipline."}
          </div>
        </div>
      </div>
    </div>,
    document.body
  );
}

function PlanDetailModal({ plan, repProfile, onClose }) {
  const [activeTab, setActiveTab] = useState("description");

  // Derive month-by-month performance for this plan from rep's monthly trend
  // Since we don't have per-plan monthly API, we approximate: plan share × monthly revenue
  const totalPlanValue = (repProfile?.plans || []).reduce((s, p) => s + p.value, 0) || 1;
  const planShare = plan.value / totalPlanValue;
  const monthlyTrend = (repProfile?.monthly_trend || []).map(m => ({
    period: m.period,
    revenue: Math.round(m.revenue * planShare),
  }));

  // Build commission tiers for this plan
  const commissionTiers = [
    { range: "≥ 120% attainment", rate: "10% (Accelerated)", color: "#1D9E75" },
    { range: "100 – 119%",        rate: "8% (On-Target)",    color: "#378ADD" },
    { range: "80 – 99%",          rate: "5% (Ramping)",      color: "#EF9F27" },
    { range: "< 80%",             rate: "3% (Below Threshold)", color: "#D85A30" },
  ];

  const planRevenue = plan.value;
  const repQuota = repProfile?.performance?.quota || 0;
  const planQuotaShare = repQuota > 0 ? planRevenue / repQuota * 100 : 0;
  const relatedRules = (repProfile?.assigned_rules || []).filter((rule) => (rule.plan_name || "").toLowerCase() === (plan.name || "").toLowerCase());
  const planHealthSummary = planQuotaShare >= 40
    ? "High-impact plan for this rep. Tight rule clarity and stage hygiene are critical to payout quality."
    : planQuotaShare >= 20
      ? "Material plan contribution. Keep coverage healthy and monitor conversion on late-stage deals."
      : "Secondary plan for this rep. Use as a growth lever through focused product plays.";

  return createPortal(
    <div style={{
      position: "fixed", inset: 0, background: "rgba(0,0,0,0.45)", zIndex: 9999,
      display: "flex", alignItems: "center", justifyContent: "center",
    }} onClick={onClose}>
      <div style={{
        background: "var(--color-background-primary)", borderRadius: "var(--border-radius-lg)",
        border: "0.5px solid var(--color-border-secondary)",
        boxShadow: "0 24px 64px rgba(0,0,0,0.22)",
        width: 580, maxHeight: "85vh",
        overflow: "hidden", display: "flex", flexDirection: "column",
      }} onClick={e => e.stopPropagation()}>
        {/* Modal header */}
        <div style={{ padding: "16px 20px", borderBottom: "0.5px solid var(--color-border-tertiary)", display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
          <div>
            <div style={{ fontSize: 15, fontWeight: 600 }}>{plan.name}</div>
            <div style={{ fontSize: 12, color: "var(--color-text-secondary)", marginTop: 2 }}>
              {plan.deal_count} deal{plan.deal_count !== 1 ? "s" : ""} · {fmt(plan.value)} total value
            </div>
          </div>
          <button onClick={onClose} style={{ background: "none", border: "none", cursor: "pointer", fontSize: 18, color: "var(--color-text-secondary)", lineHeight: 1, padding: "0 4px" }}>×</button>
        </div>

        {/* Tabs */}
        <div style={{ display: "flex", borderBottom: "0.5px solid var(--color-border-tertiary)", padding: "0 20px" }}>
          {["description", "performance"].map(t => (
            <button key={t} onClick={() => setActiveTab(t)} style={{
              padding: "8px 16px", fontSize: 13, cursor: "pointer", border: "none",
              borderBottom: activeTab === t ? "2px solid var(--color-text-primary)" : "2px solid transparent",
              background: "none", fontFamily: "var(--font-sans)", marginBottom: -1,
              color: activeTab === t ? "var(--color-text-primary)" : "var(--color-text-secondary)",
              fontWeight: activeTab === t ? 500 : 400, textTransform: "capitalize",
            }}>{t}</button>
          ))}
        </div>

        {/* Content */}
        <div style={{ padding: "20px", overflowY: "auto", flex: 1 }}>
          {activeTab === "description" && (
            <div style={{ display: "grid", gap: 16 }}>
              {/* Plan summary cards */}
              <div style={{ display: "grid", gridTemplateColumns: "repeat(3,1fr)", gap: 10 }}>
                <MetricCard label="Total Value" value={fmt(plan.value)} />
                <MetricCard label="Deals" value={String(plan.deal_count)} />
                <MetricCard label="% of Rep Quota" value={pct(planQuotaShare)} />
              </div>

              <div style={{ fontSize: 12, color: "var(--color-text-secondary)", padding: "8px 10px", borderRadius: 6, background: "var(--color-background-secondary)", border: "0.5px solid var(--color-border-tertiary)" }}>
                {planHealthSummary}
              </div>

              {/* Plan details */}
              <div style={{ border: "0.5px solid var(--color-border-tertiary)", borderRadius: "var(--border-radius-md)", padding: 14 }}>
                <div style={{ fontSize: 12, fontWeight: 500, color: "var(--color-text-secondary)", textTransform: "uppercase", letterSpacing: "0.5px", marginBottom: 10 }}>Plan Details</div>
                {[
                  ["Plan Name", plan.name],
                  ["Deals Under Plan", plan.deal_count],
                  ["Total Revenue Credited", fmt(plan.value)],
                  ["Avg Deal Size", plan.deal_count > 0 ? fmt(Math.round(plan.value / plan.deal_count)) : "—"],
                  ["Plan Coverage", `${pct(planQuotaShare)} of rep quota`],
                ].map(([label, val]) => (
                  <div key={label} style={{ display: "flex", justifyContent: "space-between", padding: "5px 0", borderBottom: "0.5px solid var(--color-border-tertiary)", fontSize: 13 }}>
                    <span style={{ color: "var(--color-text-secondary)" }}>{label}</span>
                    <span style={{ fontWeight: 500 }}>{val}</span>
                  </div>
                ))}
              </div>

              {/* Commission tiers */}
              <div style={{ border: "0.5px solid var(--color-border-tertiary)", borderRadius: "var(--border-radius-md)", padding: 14 }}>
                <div style={{ fontSize: 12, fontWeight: 500, color: "var(--color-text-secondary)", textTransform: "uppercase", letterSpacing: "0.5px", marginBottom: 10 }}>Commission Schedule</div>
                {commissionTiers.map(({ range, rate, color }) => (
                  <div key={range} style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "6px 0", borderBottom: "0.5px solid var(--color-border-tertiary)", fontSize: 13 }}>
                    <span style={{ color: "var(--color-text-secondary)" }}>{range}</span>
                    <span style={{ fontWeight: 500, color }}>{rate}</span>
                  </div>
                ))}
              </div>

              {relatedRules.length > 0 && (
                <div style={{ border: "0.5px solid var(--color-border-tertiary)", borderRadius: "var(--border-radius-md)", padding: 14 }}>
                  <div style={{ fontSize: 12, fontWeight: 500, color: "var(--color-text-secondary)", textTransform: "uppercase", letterSpacing: "0.5px", marginBottom: 10 }}>Assigned Rules</div>
                  {relatedRules.map((rule) => (
                    <div key={rule.rule_id} style={{ display: "flex", justifyContent: "space-between", padding: "5px 0", borderBottom: "0.5px solid var(--color-border-tertiary)", fontSize: 12 }}>
                      <span style={{ color: "var(--color-text-secondary)" }}>{rule.name}</span>
                      <span>{rule.threshold_min ?? "-"}% - {rule.threshold_max ?? "-"}% · {rule.rate != null ? pct(Number(rule.rate) * 100) : "n/a"}</span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}

          {activeTab === "performance" && (
            <div style={{ display: "grid", gap: 16 }}>
              {/* Performance KPIs */}
              <div style={{ display: "grid", gridTemplateColumns: "repeat(2,1fr)", gap: 10 }}>
                <MetricCard label="Revenue (This Plan)" value={fmt(plan.value)} sub={`${plan.deal_count} deals closed`} color="#1D9E75" />
                <MetricCard label="Avg Deal Size" value={plan.deal_count > 0 ? fmt(Math.round(plan.value / plan.deal_count)) : "—"} sub="Per closed deal" />
              </div>

              {/* Monthly revenue chart for this plan */}
              <div style={{ border: "0.5px solid var(--color-border-tertiary)", borderRadius: "var(--border-radius-md)", padding: 14 }}>
                <div style={{ fontSize: 13, fontWeight: 500, marginBottom: 10 }}>Estimated Monthly Revenue Under Plan</div>
                <div style={{ fontSize: 11, color: "var(--color-text-secondary)", marginBottom: 10 }}>Approximated as {pct(planShare * 100)} of rep total monthly revenue</div>
                {monthlyTrend.length === 0 ? (
                  <div style={{ fontSize: 12, color: "var(--color-text-secondary)" }}>No monthly data available.</div>
                ) : (
                  <ResponsiveContainer width="100%" height={180}>
                    <BarChart data={monthlyTrend}>
                      <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border-tertiary)" />
                      <XAxis dataKey="period" tick={{ fontSize: 10 }} tickFormatter={v => v.slice(5)} />
                      <YAxis tick={{ fontSize: 10 }} tickFormatter={v => fmt(v)} />
                      <Tooltip formatter={v => [fmt(v), "Revenue"]} />
                      <Bar dataKey="revenue" fill="#378ADD" radius={[3,3,0,0]} />
                    </BarChart>
                  </ResponsiveContainer>
                )}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>,
    document.body
  );
}

function RepProfilePanel({ repId }) {
  const { data: profile, loading, error } = useFetch(`/analytics/reps/${repId}/profile`);
  const [selectedPlan, setSelectedPlan] = useState(null);
  const [selectedProduct, setSelectedProduct] = useState(null);

  if (loading) return <Skeleton h={340} />;
  if (error) return <div style={{ color: "#D85A30", fontSize: 12 }}>Failed to load rep profile: {error}</div>;
  if (!profile) return null;

  const p = profile.performance;
  const attColor = p.attainment_pct >= 100 ? "#1D9E75" : p.attainment_pct >= 75 ? "#EF9F27" : "#D85A30";
  const productPerformanceByName = {};
  (profile.product_performance || []).forEach((item) => {
    productPerformanceByName[String(item.product || "").toLowerCase()] = item;
  });
  const ruleChartData = (profile.assigned_rules || []).slice(0, 8).map((rule) => ({
    name: rule.name,
    rate_pct: Number(rule.rate || 0) * 100,
  }));

  return (
    <div style={{ display: "grid", gap: 14 }}>
      {/* Header — name + position badge */}
      <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 2 }}>
        <div>
          <div style={{ fontSize: 16, fontWeight: 600 }}>{profile.name}</div>
          <div style={{ display: "flex", gap: 8, marginTop: 4, flexWrap: "wrap" }}>
            {profile.position && (
              <span style={{ fontSize: 11, padding: "2px 8px", borderRadius: 12, background: "var(--color-accent-primary)", color: "#fff", fontWeight: 500 }}>
                {profile.position}
              </span>
            )}
            {profile.position_level && (
              <span style={{ fontSize: 11, padding: "2px 8px", borderRadius: 12, background: "#EEF4FB", border: "1px solid #C0D8F0", color: "#2563EB", fontWeight: 500 }}>
                {profile.position_level}
              </span>
            )}
            {profile.manager_name && (
              <span style={{ fontSize: 11, color: "var(--color-text-secondary)" }}>
                Reports to: <strong>{profile.manager_name}</strong>
              </span>
            )}
          </div>
        </div>
      </div>

      {/* KPI row */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(4,1fr)", gap: 10 }}>
        <MetricCard label="Revenue" value={fmt(p.revenue)} sub={`Quota: ${fmt(p.quota)}`} />
        <MetricCard label="Attainment" value={pct(p.attainment_pct)} color={attColor} sub={`Rank #${profile.rank} of ${profile.total_reps}`} />
        <MetricCard label="Win Rate" value={pct(p.win_rate)} sub={`${p.deals_won}W / ${p.deals_lost}L`} color={p.win_rate >= 50 ? "#1D9E75" : "#D85A30"} />
        <MetricCard label="Open Pipeline" value={fmt(p.open_pipeline)} sub={profile.commission_tier} />
      </div>

      {/* Info + Plans row */}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 14 }}>
        {/* Rep info */}
        <div style={{ border: "0.5px solid var(--color-border-tertiary)", borderRadius: "var(--border-radius-lg)", padding: 14 }}>
          <div style={{ fontSize: 12, fontWeight: 500, marginBottom: 10, color: "var(--color-text-secondary)", textTransform: "uppercase", letterSpacing: "0.5px" }}>Rep Details</div>
          {[
            ["Position", profile.position || "—"],
            ["Territory / Region", profile.region || "—"],
            ["Team", profile.team_name || "—"],
            ["Reports To", profile.manager_name || "—"],
            ["Email", profile.email],
            ["Hire Date", profile.hire_date || "—"],
            ["Commission Rule", profile.commission_tier],
          ].map(([label, value]) => (
            <div key={label} style={{ display: "flex", justifyContent: "space-between", padding: "5px 0", borderBottom: "0.5px solid var(--color-border-tertiary)", fontSize: 13 }}>
              <span style={{ color: "var(--color-text-secondary)" }}>{label}</span>
              <span style={{ fontWeight: 500, textAlign: "right", maxWidth: "60%", wordBreak: "break-all" }}>{value}</span>
            </div>
          ))}
        </div>

        {/* Plans & Rules */}
        <div style={{ border: "0.5px solid var(--color-border-tertiary)", borderRadius: "var(--border-radius-lg)", padding: 14 }}>
          <div style={{ fontSize: 12, fontWeight: 500, marginBottom: 10, color: "var(--color-text-secondary)", textTransform: "uppercase", letterSpacing: "0.5px" }}>Plans Assigned</div>
          {profile.plans.length === 0 && <div style={{ fontSize: 12, color: "var(--color-text-secondary)" }}>No plan data available.</div>}
          {profile.plans.map((plan) => (
            <div
              key={plan.name}
              onClick={() => setSelectedPlan(plan)}
              style={{
                display: "flex", justifyContent: "space-between", alignItems: "center",
                padding: "7px 8px", marginLeft: -8, marginRight: -8,
                borderRadius: "var(--border-radius-md)", borderBottom: "0.5px solid var(--color-border-tertiary)",
                cursor: "pointer", transition: "background 0.1s",
              }}
              onMouseEnter={e => e.currentTarget.style.background = "var(--color-background-secondary)"}
              onMouseLeave={e => e.currentTarget.style.background = "transparent"}
            >
              <div>
                <div style={{ fontSize: 13, fontWeight: 500 }}>{plan.name}</div>
                <div style={{ fontSize: 11, color: "var(--color-text-secondary)" }}>{plan.deal_count} deal{plan.deal_count !== 1 ? "s" : ""}</div>
              </div>
              <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                <div style={{ fontSize: 13, color: "#378ADD", fontWeight: 500 }}>{fmt(plan.value)}</div>
                <span style={{ fontSize: 11, color: "var(--color-text-secondary)" }}>›</span>
              </div>
            </div>
          ))}
          {profile.plans.length > 0 && (
            <div style={{ fontSize: 11, color: "var(--color-text-secondary)", marginTop: 6, textAlign: "center" }}>Click a plan to view details</div>
          )}
          <div style={{ marginTop: 12, fontSize: 12, fontWeight: 500, marginBottom: 6, color: "var(--color-text-secondary)", textTransform: "uppercase", letterSpacing: "0.5px" }}>Commission Tiers</div>
          {[
            ["≥ 120% attainment", "10% (Accelerated)"],
            ["100 – 119%", "8% (On-Target)"],
            ["80 – 99%", "5% (Ramping)"],
            ["< 80%", "3% (Below Threshold)"],
          ].map(([range, rate]) => (
            <div key={range} style={{ display: "flex", justifyContent: "space-between", padding: "4px 0", fontSize: 12 }}>
              <span style={{ color: "var(--color-text-secondary)" }}>{range}</span>
              <span>{rate}</span>
            </div>
          ))}
        </div>
      </div>

      {/* Assigned Products */}
      {(profile.assigned_products || []).length > 0 && (
        <div style={{ border: "0.5px solid var(--color-border-tertiary)", borderRadius: "var(--border-radius-lg)", padding: 14 }}>
          <div style={{ fontSize: 12, fontWeight: 500, marginBottom: 10, color: "var(--color-text-secondary)", textTransform: "uppercase", letterSpacing: "0.5px" }}>Assigned Products</div>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
            {(profile.assigned_products || []).map((prod) => (
              <button
                key={prod.product_id || prod.name}
                onClick={() => {
                  const perf = productPerformanceByName[String(prod.name || "").toLowerCase()] || null;
                  setSelectedProduct({ ...prod, performance: perf });
                }}
                style={{
                padding: "5px 12px", borderRadius: 16,
                border: `1px solid ${prod.is_primary ? "var(--color-accent-primary)" : "var(--color-border-tertiary)"}`,
                background: prod.is_primary ? "rgba(55,138,221,0.08)" : "transparent",
                fontSize: 12,
                display: "flex", alignItems: "center", gap: 6,
                cursor: "pointer",
              }}
              >
                {prod.is_primary && <span style={{ width: 6, height: 6, borderRadius: "50%", background: "var(--color-accent-primary)", display: "inline-block" }} />}
                <span style={{ fontWeight: prod.is_primary ? 600 : 400 }}>{prod.name}</span>
                {prod.sku && <span style={{ fontSize: 10, color: "var(--color-text-secondary)" }}>{prod.sku}</span>}
                {prod.specialization && prod.specialization !== "primary_seller" && (
                  <span style={{ fontSize: 10, color: "var(--color-text-secondary)", fontStyle: "italic" }}>{prod.specialization}</span>
                )}
              </button>
            ))}
          </div>
          <div style={{ fontSize: 11, color: "var(--color-text-secondary)", marginTop: 8 }}>
            Click a product to inspect revenue mix, stage distribution, and conversion quality.
          </div>
        </div>
      )}

      {(profile.assigned_rules || []).length > 0 && (
        <div style={{ border: "0.5px solid var(--color-border-tertiary)", borderRadius: "var(--border-radius-lg)", padding: 14 }}>
          <div style={{ fontSize: 12, fontWeight: 500, marginBottom: 8, color: "var(--color-text-secondary)", textTransform: "uppercase", letterSpacing: "0.5px" }}>Assigned Rules & Rates</div>
          <div style={{ width: "100%", height: 160, marginBottom: 8 }}>
            <ResponsiveContainer>
              <BarChart data={ruleChartData} margin={{ left: 0, right: 10, top: 5, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border-tertiary)" />
                <XAxis dataKey="name" tick={{ fontSize: 10 }} />
                <YAxis tick={{ fontSize: 10 }} tickFormatter={(v) => `${v}%`} />
                <Tooltip formatter={(v) => `${Number(v || 0).toFixed(2)}%`} />
                <Bar dataKey="rate_pct" fill="#378ADD" radius={[3, 3, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
          <div style={{ display: "grid", gap: 4 }}>
            {(profile.assigned_rules || []).slice(0, 6).map((rule) => (
              <div key={rule.rule_id} style={{ display: "flex", justifyContent: "space-between", fontSize: 11, borderBottom: "0.5px solid var(--color-border-tertiary)", padding: "4px 0" }}>
                <span style={{ color: "var(--color-text-secondary)" }}>{rule.plan_name} · {rule.name}</span>
                <span>{rule.threshold_min ?? "-"}% - {rule.threshold_max ?? "-"}% · {rule.rate != null ? pct(Number(rule.rate) * 100) : "n/a"}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Monthly revenue chart */}
      <div style={{ border: "0.5px solid var(--color-border-tertiary)", borderRadius: "var(--border-radius-lg)", padding: 14 }}>
        <div style={{ fontSize: 13, fontWeight: 500, marginBottom: 12 }}>Monthly Revenue Performance</div>
        {profile.monthly_trend.length === 0 ? (
          <div style={{ fontSize: 12, color: "var(--color-text-secondary)" }}>No revenue data available.</div>
        ) : (
          <>
            <ResponsiveContainer width="100%" height={200}>
              <BarChart data={profile.monthly_trend}>
                <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border-tertiary)" />
                <XAxis dataKey="period" tick={{ fontSize: 10 }} tickFormatter={v => v.slice(5)} />
                <YAxis tick={{ fontSize: 11 }} tickFormatter={v => fmt(v)} />
                <Tooltip formatter={v => [fmt(v), "Revenue"]} labelFormatter={l => `Month: ${l}`} />
                <Bar dataKey="revenue" fill="#378ADD" radius={[3, 3, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
            {(() => {
              const trend = profile.monthly_trend;
              if (trend.length < 2) return null;
              const vals = trend.map(t => t.revenue || 0);
              const best = trend.reduce((a, b) => (b.revenue > a.revenue ? b : a));
              const worst = trend.reduce((a, b) => (b.revenue < a.revenue ? b : a));
              const last = vals[vals.length - 1];
              const prev = vals[vals.length - 2];
              const mom = prev > 0 ? ((last - prev) / prev) * 100 : null;
              // Simple linear trend: compare first-half avg vs second-half avg
              const mid = Math.floor(vals.length / 2);
              const firstHalf = vals.slice(0, mid).reduce((s, v) => s + v, 0) / mid;
              const secondHalf = vals.slice(mid).reduce((s, v) => s + v, 0) / (vals.length - mid);
              const trendPct = firstHalf > 0 ? ((secondHalf - firstHalf) / firstHalf) * 100 : null;
              const trendLabel = trendPct == null ? null : trendPct >= 10 ? "strong upward" : trendPct >= 2 ? "modest upward" : trendPct <= -10 ? "declining" : trendPct <= -2 ? "softening" : "flat";
              const trendColor = trendPct == null ? "var(--color-text-secondary)" : trendPct >= 2 ? "#1D9E75" : trendPct <= -2 ? "#D85A30" : "#EF9F27";
              return (
                <div style={{ marginTop: 10, padding: "8px 10px", borderRadius: 6, background: "rgba(55,138,221,0.06)", border: "0.5px solid rgba(55,138,221,0.2)", fontSize: 11, color: "var(--color-text-secondary)", display: "flex", flexWrap: "wrap", gap: "6px 16px" }}>
                  {mom != null && (
                    <span>MoM: <strong style={{ color: mom >= 0 ? "#1D9E75" : "#D85A30" }}>{mom >= 0 ? "+" : ""}{mom.toFixed(1)}%</strong></span>
                  )}
                  {trendLabel && (
                    <span>Trend: <strong style={{ color: trendColor }}>{trendLabel}</strong> over {trend.length} months</span>
                  )}
                  <span>Peak: <strong>{best.period.slice(5)}</strong> at {fmt(best.revenue)}</span>
                  {worst.period !== best.period && (
                    <span>Trough: <strong>{worst.period.slice(5)}</strong> at {fmt(worst.revenue)}</span>
                  )}
                  {trendLabel === "declining" || trendLabel === "softening" ? (
                    <span style={{ color: "#D85A30" }}>⚠ Revenue has softened — review pipeline coverage and deal velocity.</span>
                  ) : trendLabel === "strong upward" ? (
                    <span style={{ color: "#1D9E75" }}>Rep is on an accelerating run — consider stretch quota or promotion review.</span>
                  ) : null}
                </div>
              );
            })()}
          </>
        )}
      </div>

      {/* Plan detail modal */}
      {selectedPlan && (
        <PlanDetailModal
          plan={selectedPlan}
          repProfile={profile}
          onClose={() => setSelectedPlan(null)}
        />
      )}

      {selectedProduct && (
        <ProductDetailModal
          product={selectedProduct}
          performance={selectedProduct.performance}
          onClose={() => setSelectedProduct(null)}
        />
      )}
    </div>
  );
}

function LeadershipRollupPanel({ leadership }) {
  if (!leadership || leadership.length === 0) return null;

  return (
    <div style={{ border: "0.5px solid var(--color-border-tertiary)", borderRadius: "var(--border-radius-lg)", padding: 16 }}>
      <div style={{ fontSize: 13, fontWeight: 500, marginBottom: 4 }}>Leadership view — team attainment rollup</div>
      <div style={{ fontSize: 11, color: "var(--color-text-secondary)", marginBottom: 12 }}>
        Revenue, quota and attainment aggregated across each leader's full reporting chain
      </div>
      <div style={{ overflowX: "auto" }}>
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
          <thead>
            <tr style={{ color: "var(--color-text-secondary)", fontSize: 11, textTransform: "uppercase", letterSpacing: "0.4px" }}>
              {["Name", "Title", "Plan", "Team Size", "Team Revenue", "Team Quota", "Attainment", "Avg Rep Att.", "Win Rate"].map(h => (
                <th key={h} style={{ textAlign: h === "Name" || h === "Title" || h === "Plan" ? "left" : "right", padding: "5px 10px", fontWeight: 400 }}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {leadership.map(l => {
              const attColor = l.team_attainment_pct >= 100 ? "#1D9E75" : l.team_attainment_pct >= 75 ? "#EF9F27" : "#D85A30";
              const avgColor = l.avg_rep_attainment_pct >= 100 ? "#1D9E75" : l.avg_rep_attainment_pct >= 75 ? "#EF9F27" : "#D85A30";
              return (
                <tr key={l.user_id} style={{ borderTop: "0.5px solid var(--color-border-tertiary)" }}>
                  <td style={{ padding: "7px 10px", fontWeight: 500 }}>{l.name}</td>
                  <td style={{ padding: "7px 10px", color: "var(--color-text-secondary)", fontSize: 11 }}>{l.position}</td>
                  <td style={{ padding: "7px 10px", color: "var(--color-text-secondary)", fontSize: 11 }}>{l.plan_name}</td>
                  <td style={{ textAlign: "right", padding: "7px 10px" }}>{l.team_rep_count}</td>
                  <td style={{ textAlign: "right", padding: "7px 10px" }}>{fmt(l.team_revenue)}</td>
                  <td style={{ textAlign: "right", padding: "7px 10px" }}>{l.team_quota > 0 ? fmt(l.team_quota) : <span style={{ color: "var(--color-text-secondary)" }}>—</span>}</td>
                  <td style={{ textAlign: "right", padding: "7px 10px", fontWeight: 600, color: attColor }}>{l.team_quota > 0 ? pct(l.team_attainment_pct) : <span style={{ color: "var(--color-text-secondary)", fontWeight: 400, fontSize: 11 }}>No plan</span>}</td>
                  <td style={{ textAlign: "right", padding: "7px 10px", color: avgColor }}>{l.avg_rep_attainment_pct > 0 ? pct(l.avg_rep_attainment_pct) : "—"}</td>
                  <td style={{ textAlign: "right", padding: "7px 10px" }}>{l.win_rate > 0 ? pct(l.win_rate) : "—"}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {leadership.length > 0 && (() => {
        const cro = leadership.find(l => l.position_level === "Executive");
        if (!cro || cro.team_quota <= 0) return null;
        const gap = cro.team_quota - cro.team_revenue;
        const runRate = cro.team_revenue / Math.max(1, new Date().getMonth() + 1) * 12;
        const onTrack = cro.team_attainment_pct >= 75;
        return (
          <div style={{ marginTop: 10, padding: "8px 10px", borderRadius: 6, background: onTrack ? "rgba(29,158,117,0.06)" : "rgba(216,90,48,0.06)", border: `0.5px solid ${onTrack ? "#1D9E7530" : "#D85A3030"}`, fontSize: 11, color: "var(--color-text-secondary)" }}>
            Company-wide: <strong style={{ color: onTrack ? "#1D9E75" : "#D85A30" }}>{pct(cro.team_attainment_pct)} attainment</strong> across {cro.team_rep_count} sellers.
            {" "}Revenue gap to plan: <strong>{fmt(Math.max(0, gap))}</strong>.
            {" "}Full-year run rate: <strong>{fmt(runRate)}</strong> vs quota <strong>{fmt(cro.team_quota)}</strong>.
            {onTrack
              ? " Team is on track — maintain deal velocity and protect key pipeline."
              : " Below pace — review top-of-funnel health, rep capacity, and high-risk deals."}
          </div>
        );
      })()}
    </div>
  );
}

// ── Rep Performance Tab ───────────────────────────────────────────────────
function RepsTab({ refreshKey, period, userRole, activeCompany }) {
  const role = userRole || "executive";
  const company = activeCompany || "";
  const repsUrl = withPeriod(withRefresh("/analytics/reps/performance", refreshKey), period);
  const leadershipUrl = withPeriod(withRefresh("/analytics/reps/leadership", refreshKey), period);
  const { data: reps, loading: rLoading, error: repsError } = useFetch(repsUrl, { role, company });
  const { data: leadershipData, loading: lLoading } = useFetch(leadershipUrl, { role, company });
  const { data: clusters, loading: cLoading, error: clusterError } = useFetch(withRefresh("/ml/cluster/reps", refreshKey), { role, company });
  const [activeRepId, setActiveRepId] = useState(null);
  const [winRateView, setWinRateView] = useState("rep");
  const [clusterView, setClusterView] = useState("rep");

  const leadershipRows = useMemo(() => {
    if (Array.isArray(leadershipData)) return leadershipData;
    return leadershipData?.leaders || [];
  }, [leadershipData]);

  const managerRepIds = useMemo(() => {
    const ids = new Set();
    for (const leader of leadershipRows) {
      if (leader?.rep_id) ids.add(String(leader.rep_id));
    }
    for (const rep of reps || []) {
      const title = String(rep?.position || "").toLowerCase();
      const level = String(rep?.position_level || "").toLowerCase();
      if (title.includes("manager") || level.includes("management")) {
        ids.add(String(rep.rep_id || ""));
      }
    }
    return ids;
  }, [reps, leadershipRows]);

  const clusterRows = useMemo(() => {
    const rows = clusters?.clusters || [];
    if (clusterView === "manager") {
      return rows.filter((row) => managerRepIds.has(String(row.rep_id || "")));
    }
    return rows;
  }, [clusters, clusterView, managerRepIds]);

  const personaCounts = useMemo(() => {
    const counts = {};
    for (const row of clusterRows) {
      const key = row?.persona || "Unknown";
      counts[key] = (counts[key] || 0) + 1;
    }
    return counts;
  }, [clusterRows]);

  const legendEntries = useMemo(() => {
    return Object.entries(personaCounts)
      .sort((a, b) => b[1] - a[1])
      .map(([persona]) => ({
        persona,
        color: PERSONA_COLORS[persona] || "#888",
      }));
  }, [personaCounts]);

  const repWinRateRows = useMemo(() => {
    return (reps || []).map((row) => {
      const dealsWon = Number(row?.deals_won || 0);
      const avgDealSize = Number(
        row?.average_deal_size
        ?? row?.avg_deal_size
        ?? (dealsWon > 0 ? Number(row?.revenue || 0) / dealsWon : 0)
      );
      return {
        rep_id: String(row?.rep_id || ""),
        name: row?.name || "Rep",
        win_rate: Number(row?.win_rate || 0),
        avg_deal_size: Number.isFinite(avgDealSize) ? avgDealSize : 0,
        open_pipeline: Number(row?.open_pipeline || 0),
      };
    });
  }, [reps]);

  const managerWinRateRows = useMemo(() => {
    return leadershipRows
      .map((row) => {
        const won = Number(row?.team_won_deals || 0);
        const teamRevenue = Number(row?.team_revenue || 0);
        const avgDealSize = won > 0 ? teamRevenue / won : 0;
        return {
          rep_id: String(row?.rep_id || row?.user_id || ""),
          name: row?.name || "Manager",
          win_rate: Number(row?.win_rate || 0),
          avg_deal_size: Number.isFinite(avgDealSize) ? avgDealSize : 0,
          open_pipeline: null,
          team_rep_count: Number(row?.team_rep_count || 0),
        };
      })
      .filter((row) => row.team_rep_count > 0);
  }, [leadershipRows]);

  const winRateRows = useMemo(() => {
    return winRateView === "manager" ? managerWinRateRows : repWinRateRows;
  }, [winRateView, managerWinRateRows, repWinRateRows]);

  const plottableWinRateRows = useMemo(
    () => winRateRows.filter((row) => Number(row.avg_deal_size || 0) > 0),
    [winRateRows]
  );

  const zeroDealSizeHiddenCount = Math.max(0, winRateRows.length - plottableWinRateRows.length);

  // Set first rep as default once data loads
  useEffect(() => {
    if (reps && reps.length > 0 && !activeRepId) {
      setActiveRepId(reps[0].rep_id);
    }
  }, [reps, activeRepId]);

  // Reset selection when refreshKey changes (company switch)
  useEffect(() => {
    setActiveRepId(null);
  }, [refreshKey]);

  return (
    <div style={{ display: "grid", gap: 16 }}>
      {/* Rep Tab Bar */}
      {rLoading ? <Skeleton h={38} /> : repsError ? (
        <div style={{ fontSize: 12, color: "#D85A30" }}>Rep list unavailable: {repsError}</div>
      ) : (
        <div style={{ display: "flex", gap: 0, borderBottom: "0.5px solid var(--color-border-tertiary)", overflowX: "auto" }}>
          {(reps || []).map((rep) => {
            const isActive = rep.rep_id === activeRepId;
            const attColor = rep.attainment_pct >= 100 ? "#1D9E75" : rep.attainment_pct >= 75 ? "#EF9F27" : "#D85A30";
            return (
              <button key={rep.rep_id} onClick={() => setActiveRepId(rep.rep_id)} style={{
                padding: "8px 16px", fontSize: 12, cursor: "pointer", border: "none",
                borderBottom: isActive ? "2px solid var(--color-text-primary)" : "2px solid transparent",
                background: "none", fontFamily: "var(--font-sans)", marginBottom: -1,
                color: isActive ? "var(--color-text-primary)" : "var(--color-text-secondary)",
                fontWeight: isActive ? 500 : 400, whiteSpace: "nowrap", display: "flex", flexDirection: "column", alignItems: "center", gap: 2,
              }}>
                <span>{rep.name}</span>
                {rep.position && <span style={{ fontSize: 10, color: "var(--color-text-secondary)", fontStyle: "italic" }}>{rep.position}</span>}
                <span style={{ fontSize: 10, color: attColor }}>{pct(rep.attainment_pct)}</span>
              </button>
            );
          })}
        </div>
      )}

      {/* Selected Rep Profile */}
      {activeRepId && <RepProfilePanel key={activeRepId} repId={activeRepId} />}

      {/* Overview section — cluster + table */}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16 }}>
        {/* D8: Win rate by deal size scatter */}
        <div style={{ border: "0.5px solid var(--color-border-tertiary)", borderRadius: "var(--border-radius-lg)", padding: 16 }}>
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 10, marginBottom: 4 }}>
            <div style={{ fontSize: 13, fontWeight: 500 }}>Win rate by deal size</div>
            <select
              value={winRateView}
              onChange={(e) => setWinRateView(e.target.value)}
              style={{
                fontSize: 12,
                padding: "5px 8px",
                borderRadius: 8,
                border: "0.5px solid var(--color-border-secondary)",
                background: "var(--color-background-primary)",
                color: "var(--color-text-primary)",
              }}
            >
              <option value="rep">Rep persona cluster</option>
              <option value="manager">Manager level persona cluster</option>
            </select>
          </div>
          <div style={{ fontSize: 11, color: "var(--color-text-secondary)", marginBottom: 10 }}>Each dot = one rep (bubble size = pipeline)</div>
          {(rLoading || (winRateView === "manager" && lLoading)) ? <Skeleton h={200} /> : winRateRows.length === 0 ? (
            <div style={{ fontSize: 12, color: "var(--color-text-secondary)" }}>
              {winRateView === "manager" ? "No manager-level data available." : "No rep data."}
            </div>
          ) : plottableWinRateRows.length === 0 ? (
            <div style={{ fontSize: 12, color: "var(--color-text-secondary)" }}>
              No rows with non-zero average deal size are available for this view.
            </div>
          ) : (
            <ResponsiveContainer width="100%" height={220}>
              <ScatterChart>
                <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border-tertiary)" />
                <XAxis dataKey="avg_deal_size" name="Avg Deal Size" tick={{ fontSize: 10 }} tickFormatter={v => fmt(v)} label={{ value: "Avg Deal Size", position: "insideBottom", offset: -5, fontSize: 10 }} />
                <YAxis dataKey="win_rate" name="Win Rate %" tick={{ fontSize: 10 }} tickFormatter={v => `${v.toFixed(0)}%`} domain={[0, 100]} />
                <Tooltip content={({ payload }) => payload?.[0] ? (
                  <div style={{ background: "var(--color-background-primary)", border: "0.5px solid var(--color-border-tertiary)", borderRadius: 8, padding: "8px 12px", fontSize: 12 }}>
                    <div style={{ fontWeight: 500 }}>{payload[0].payload.name}</div>
                    <div>Deal size: {payload[0].payload.avg_deal_size > 0 ? fmt(payload[0].payload.avg_deal_size) : "—"}</div>
                    <div>Win rate: {pct(payload[0].payload.win_rate)}</div>
                    {payload[0].payload.team_rep_count
                      ? <div>Team reps: {payload[0].payload.team_rep_count}</div>
                      : <div>Pipeline: {fmt(payload[0].payload.open_pipeline || 0)}</div>}
                  </div>
                ) : null} />
                <Scatter
                  data={plottableWinRateRows}
                  fill="#378ADD"
                >
                  {plottableWinRateRows.map((r, i) => {
                    const c = Number(r.win_rate || 0) >= 60 ? "#1D9E75" : Number(r.win_rate || 0) >= 40 ? "#EF9F27" : "#D85A30";
                    return <Cell key={i} fill={c} fillOpacity={0.75} />;
                  })}
                </Scatter>
              </ScatterChart>
            </ResponsiveContainer>
          )}
          {zeroDealSizeHiddenCount > 0 && (
            <div style={{ marginTop: 8, fontSize: 11, color: "var(--color-text-secondary)" }}>
              {zeroDealSizeHiddenCount} row{zeroDealSizeHiddenCount > 1 ? "s" : ""} hidden due to zero/unknown average deal size.
            </div>
          )}
          {plottableWinRateRows.length > 0 && (() => {
            const avgWin = plottableWinRateRows.reduce((s, r) => s + Number(r.win_rate || 0), 0) / plottableWinRateRows.length;
            const avgSize = plottableWinRateRows.reduce((s, r) => s + Number(r.avg_deal_size || 0), 0) / plottableWinRateRows.length;
            const top = [...plottableWinRateRows].sort((a, b) => Number(b.win_rate || 0) - Number(a.win_rate || 0))[0];
            const largeDeal = plottableWinRateRows.filter(r => Number(r.avg_deal_size || 0) > avgSize);
            const largeDealWin = largeDeal.length ? largeDeal.reduce((s, r) => s + (r.win_rate || 0), 0) / largeDeal.length : 0;
            const insight = largeDealWin > avgWin
              ? `Reps handling above-avg deal sizes close at ${pct(largeDealWin)} — the team scales well into larger accounts.`
              : `Reps handling above-avg deal sizes close at only ${pct(largeDealWin)} vs team avg ${pct(avgWin)} — enterprise deals need focused coaching or a dedicated overlay.`;
            return (
              <div style={{ marginTop: 10, padding: "8px 10px", borderRadius: 6, background: "rgba(55,138,221,0.06)", border: "0.5px solid rgba(55,138,221,0.2)", fontSize: 11, color: "var(--color-text-secondary)" }}>
                Team avg win rate <strong>{pct(avgWin)}</strong> · top closer <strong>{top?.name}</strong> at <strong>{pct(top?.win_rate)}</strong>. {insight}
              </div>
            );
          })()}
        </div>

        <div style={{ border: "0.5px solid var(--color-border-tertiary)", borderRadius: "var(--border-radius-lg)", padding: 16 }}>
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 10, marginBottom: 14 }}>
            <div style={{ fontSize: 13, fontWeight: 500 }}>
              {clusterView === "manager" ? "Manager level persona clusters (PCA)" : "Rep persona clusters (PCA)"}
            </div>
            <select
              value={clusterView}
              onChange={(e) => setClusterView(e.target.value)}
              style={{
                fontSize: 12,
                padding: "5px 8px",
                borderRadius: 8,
                border: "0.5px solid var(--color-border-secondary)",
                background: "var(--color-background-primary)",
                color: "var(--color-text-primary)",
              }}
            >
              <option value="rep">Rep persona cluster</option>
              <option value="manager">Manager level persona cluster</option>
            </select>
          </div>
          {cLoading ? <Skeleton /> : clusterError ? (
            <div style={{ fontSize: 12, color: "#D85A30" }}>Clustering unavailable: {clusterError}</div>
          ) : !clusters ? (
            <div style={{ fontSize: 12, color: "var(--color-text-secondary)" }}>No clustering data available.</div>
          ) : !clusterRows.length ? (
            <div style={{ fontSize: 12, color: "var(--color-text-secondary)" }}>
              {clusterView === "manager" ? "No manager level persona cluster data available." : "No rep persona cluster data available."}
            </div>
          ) : (
            <>
              <ResponsiveContainer width="100%" height={220}>
                <ScatterChart>
                  <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border-tertiary)" />
                  <XAxis dataKey="pca_x" name="PC1" tick={{ fontSize: 10 }} />
                  <YAxis dataKey="pca_y" name="PC2" tick={{ fontSize: 10 }} />
                  <Tooltip cursor={{ strokeDasharray: "3 3" }} content={({ payload }) => payload?.[0] ? (
                    <div style={{ background: "var(--color-background-primary)", border: "0.5px solid var(--color-border-tertiary)", borderRadius: 8, padding: "8px 12px", fontSize: 12 }}>
                      <div style={{ fontWeight: 500 }}>{payload[0].payload.rep_name}</div>
                      <div style={{ color: PERSONA_COLORS[payload[0].payload.persona] }}>{payload[0].payload.persona}</div>
                    </div>
                  ) : null} />
                  <Scatter data={clusterRows} fill="#378ADD">
                    {clusterRows.map((c, i) => <Cell key={i} fill={PERSONA_COLORS[c.persona] || "#888"} />)}
                  </Scatter>
                </ScatterChart>
              </ResponsiveContainer>
              <div style={{ display: "flex", gap: 12, flexWrap: "wrap", marginTop: 8 }}>
                {legendEntries.map((entry) => (
                  <span key={entry.persona} style={{ display: "flex", alignItems: "center", gap: 4, fontSize: 11 }}>
                    <span style={{ width: 10, height: 10, borderRadius: "50%", background: entry.color, display: "inline-block" }} />{entry.persona}
                  </span>
                ))}
              </div>
              {(() => {
                const total = clusterRows.length;
                const personaTotal = Object.keys(personaCounts).length;
                const tips = Object.entries(personaCounts).map(([persona, n]) => {
                  const advice = {
                    "Top Performer": `${n} top performer${n > 1 ? "s" : ""} — leverage for enterprise deal coaching and high-stakes pursuits`,
                    "High Volume": `${n} high-volume rep${n > 1 ? "s" : ""} — optimize quality guardrails while maintaining throughput`,
                    "Rising Star": `${n} rising star${n > 1 ? "s" : ""} — provide stretch territories and advanced enablement`,
                    "Needs Coaching": `${n} rep${n > 1 ? "s" : ""} need targeted coaching on conversion and cycle discipline`,
                    "Quota At Risk": `${n} rep${n > 1 ? "s" : ""} at quota risk — prioritize manager interventions and pipeline recovery`,
                    "Leadership Oversight": `${n} leadership profile${n > 1 ? "s" : ""} shown for oversight, not IC coaching actions`,
                  };
                  return advice[persona] || `${n} ${persona}`;
                });
                return (
                  <div style={{ marginTop: 10, padding: "8px 10px", borderRadius: 6, background: "rgba(55,138,221,0.06)", border: "0.5px solid rgba(55,138,221,0.2)", fontSize: 11, color: "var(--color-text-secondary)" }}>
                    <strong>{total} reps</strong> across <strong>{personaTotal} personas</strong>. {tips.join('. ')}.
                  </div>
                );
              })()}
            </>
          )}
        </div>
        <div style={{ border: "0.5px solid var(--color-border-tertiary)", borderRadius: "var(--border-radius-lg)", padding: 16, overflowY: "auto", maxHeight: 320 }}>
          <div style={{ fontSize: 13, fontWeight: 500, marginBottom: 12 }}>Cluster diagnostics</div>
          {cLoading ? <Skeleton h={100} /> : clusterError ? (
            <div style={{ fontSize: 12, color: "#D85A30" }}>Diagnostics unavailable: {clusterError}</div>
          ) : !clusters ? (
            <div style={{ fontSize: 12, color: "var(--color-text-secondary)" }}>No diagnostics available.</div>
          ) : (
            <>
              <div style={{ fontSize: 12, color: "var(--color-text-secondary)", marginBottom: 8 }}>Optimal k = {clusters.diagnostics.optimal_k} · {clusters.model_info}</div>
              {Object.entries(clusters.diagnostics.silhouette_scores).map(([k, s]) => (
                <div key={k} style={{ display: "flex", justifyContent: "space-between", padding: "5px 0", borderBottom: "0.5px solid var(--color-border-tertiary)", fontSize: 13 }}>
                  <span>k = {k}</span><span style={{ color: k == clusters.diagnostics.optimal_k ? "#1D9E75" : "var(--color-text-secondary)", fontWeight: k == clusters.diagnostics.optimal_k ? 500 : 400 }}>silhouette = {s}</span>
                </div>
              ))}
              {(() => {
                const best = clusters.diagnostics?.silhouette_scores?.[clusters.diagnostics?.optimal_k];
                const verdict = best >= 0.5
                  ? "Well-separated personas — assignments are reliable for differentiated coaching plans."
                  : best >= 0.3
                  ? "Moderate separation — personas are directionally useful; some reps may straddle boundaries."
                  : "Weak separation — the team behaves homogeneously; persona-based coaching may have limited uplift until more data accumulates.";
                const color = best >= 0.5 ? "#1D9E75" : best >= 0.3 ? "#EF9F27" : "#D85A30";
                return (
                  <div style={{ marginTop: 10, padding: "8px 10px", borderRadius: 6, background: `${color}10`, border: `0.5px solid ${color}40`, fontSize: 11, color: "var(--color-text-secondary)" }}>
                    Best silhouette at k={clusters.diagnostics?.optimal_k}: <strong style={{ color }}>{best}</strong>. {verdict}
                  </div>
                );
              })()}
            </>
          )}
        </div>
      </div>
    </div>
  );
}

// ── Agent Tab ─────────────────────────────────────────────────────────────
function DataQualityTab({ refreshKey, activeCompany, userRole }) {
  const role = userRole || "executive";
  const company = activeCompany || "";
  const { data: summaryData, loading: sLoading, error: sError } = useFetch(withRefresh("/data-quality/summary", refreshKey), { role, company });
  const { data: checksData, loading: cLoading, error: cError } = useFetch(withRefresh("/data-quality/checks", refreshKey), { role, company });
  const [statusFilter, setStatusFilter] = useState("all");

  const checks = checksData?.checks || summaryData?.checks || [];
  const filteredChecks = checks.filter((check) => statusFilter === "all" || check.status === statusFilter);
  const generatedAt = summaryData?.generated_at || checksData?.generated_at;
  const loading = sLoading || cLoading;
  const error = sError || cError;

  if (loading) return <Skeleton h={320} />;
  if (error) {
    return <div style={{ color: "#D85A30", padding: 14 }}>Data quality checks unavailable: {error}</div>;
  }

  const statusColor =
    summaryData?.status === "PASS"
      ? "#1D9E75"
      : summaryData?.status === "WARN"
        ? "#EF9F27"
        : "#D85A30";

  return (
    <div style={{ display: "grid", gap: 14 }}>
      <div style={{ border: "0.5px solid var(--color-border-tertiary)", borderRadius: "var(--border-radius-lg)", padding: 16, background: "linear-gradient(120deg, rgba(14,35,56,0.08) 0%, rgba(55,138,221,0.08) 100%)" }}>
        <div style={{ fontSize: 11, textTransform: "uppercase", letterSpacing: "0.6px", color: "var(--color-text-secondary)", marginBottom: 4 }}>Data Quality Command</div>
        <div style={{ fontSize: 18, fontWeight: 600, marginBottom: 8 }}>Integrity Scorecard and Failing Signals</div>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(170px, 1fr))", gap: 8 }}>
          <MetricCard label="Overall Status" value={summaryData?.status || "N/A"} sub="PASS / WARN / FAIL" color={statusColor} />
          <MetricCard label="Quality Score" value={String(summaryData?.score ?? "N/A")} sub="0 to 100" color={statusColor} />
          <MetricCard label="Errors" value={String(summaryData?.error_count || 0)} sub="Failing checks" color="#D85A30" />
          <MetricCard label="Warnings" value={String(summaryData?.warning_count || 0)} sub="Needs review" color="#EF9F27" />
        </div>
      </div>

      <div style={{ border: "0.5px solid var(--color-border-tertiary)", borderRadius: "var(--border-radius-lg)", padding: 12, background: "var(--color-background-secondary)" }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 10, flexWrap: "wrap", marginBottom: 8 }}>
          <div style={{ fontSize: 12, color: "var(--color-text-secondary)" }}>
            {generatedAt ? `Generated: ${String(generatedAt).slice(0, 19).replace("T", " ")} UTC` : "Generated time unavailable"}
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <span style={{ fontSize: 11, color: "var(--color-text-secondary)" }}>Filter:</span>
            <select
              value={statusFilter}
              onChange={(e) => setStatusFilter(e.target.value)}
              style={{ padding: "6px 8px", borderRadius: "var(--border-radius-md)", border: "0.5px solid var(--color-border-secondary)", fontSize: 12, background: "var(--color-background-primary)" }}
            >
              <option value="all">All</option>
              <option value="FAIL">FAIL</option>
              <option value="WARN">WARN</option>
              <option value="PASS">PASS</option>
            </select>
          </div>
        </div>

        <div style={{ overflowX: "auto" }}>
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
            <thead>
              <tr style={{ color: "var(--color-text-secondary)", textTransform: "uppercase", letterSpacing: "0.4px", fontSize: 10 }}>
                <th style={{ textAlign: "left", padding: "6px 8px", fontWeight: 500 }}>Check</th>
                <th style={{ textAlign: "left", padding: "6px 8px", fontWeight: 500 }}>Status</th>
                <th style={{ textAlign: "left", padding: "6px 8px", fontWeight: 500 }}>Message</th>
                <th style={{ textAlign: "right", padding: "6px 8px", fontWeight: 500 }}>Affected Rows</th>
              </tr>
            </thead>
            <tbody>
              {filteredChecks.map((check) => {
                const chipColor = check.status === "PASS" ? "#1D9E75" : check.status === "WARN" ? "#EF9F27" : "#D85A30";
                return (
                  <tr key={check.name} style={{ borderTop: "0.5px solid var(--color-border-tertiary)" }}>
                    <td style={{ padding: "7px 8px", fontWeight: 500 }}>{check.name}</td>
                    <td style={{ padding: "7px 8px" }}>
                      <span style={{ border: `0.5px solid ${chipColor}`, color: chipColor, borderRadius: 999, fontSize: 10, padding: "2px 7px" }}>
                        {check.status}
                      </span>
                    </td>
                    <td style={{ padding: "7px 8px", color: "var(--color-text-secondary)" }}>{check.message}</td>
                    <td style={{ padding: "7px 8px", textAlign: "right" }}>{check.affected_rows ?? 0}</td>
                  </tr>
                );
              })}
              {filteredChecks.length === 0 && (
                <tr>
                  <td colSpan={4} style={{ padding: "10px 8px", color: "var(--color-text-secondary)" }}>
                    No checks match this filter.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

function ModelMonitoringTab({ refreshKey, activeCompany, userRole }) {
  const role = userRole || "executive";
  const company = activeCompany || "";
  const { data: runsData, loading: runsLoading, error: runsError } = useFetch(withRefresh("/ml/model-runs", refreshKey), { role, company });
  const { data: summaryData, loading: summaryLoading, error: summaryError } = useFetch(withRefresh("/ml/predictions/summary", refreshKey), { role, company });
  const { data: accuracyData, loading: accuracyLoading, error: accuracyError } = useFetch(withRefresh("/ml/forecast/accuracy", refreshKey), { role, company });
  const { data: driftData, loading: driftLoading, error: driftError } = useFetch(withRefresh("/ml/drift", refreshKey), { role, company });

  const loading = runsLoading || summaryLoading || accuracyLoading || driftLoading;
  if (loading) return <Skeleton h={320} />;

  const modelRuns = runsData?.model_runs || [];
  const latestRun = modelRuns[0] || null;
  const countsByModel = summaryData?.counts_by_model || {};
  const latestByModel = summaryData?.latest_prediction_by_model || {};
  const predictionCount = summaryData?.prediction_count || 0;
  const backtest = accuracyData?.backtest || {};

  return (
    <div style={{ display: "grid", gap: 14 }}>
      <div style={{ border: "0.5px solid var(--color-border-tertiary)", borderRadius: "var(--border-radius-lg)", padding: 16, background: "linear-gradient(120deg, rgba(14,35,56,0.08) 0%, rgba(55,138,221,0.08) 100%)" }}>
        <div style={{ fontSize: 11, textTransform: "uppercase", letterSpacing: "0.6px", color: "var(--color-text-secondary)", marginBottom: 4 }}>Model Monitoring</div>
        <div style={{ fontSize: 18, fontWeight: 600, marginBottom: 8 }}>Model Registry, Prediction Health, and Drift Signals</div>
        {(runsError || summaryError || accuracyError) && (
          <div style={{ fontSize: 12, color: "#D85A30", marginBottom: 8 }}>
            Partial data: {[runsError, summaryError, accuracyError].filter(Boolean).join(" | ")}
          </div>
        )}
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(170px, 1fr))", gap: 8 }}>
          <MetricCard label="Model Runs" value={String(modelRuns.length)} sub="Latest 100 records" />
          <MetricCard label="Predictions Logged" value={String(predictionCount)} sub="Across all models" color="#378ADD" />
          <MetricCard label="Forecast Accuracy" value={backtest?.mape != null ? `${Number(backtest.mape).toFixed(2)}%` : "N/A"} sub="MAPE from rolling backtest" color={backtest?.status === "ok" ? "#1D9E75" : "#EF9F27"} />
          <MetricCard label="Drift Status" value={driftError ? "Unavailable" : (driftData?.drifted ? "Drifted" : "Stable")} sub={driftError ? "No baseline run for drift" : (driftData?.severity || "computed")} color={driftError ? "#EF9F27" : (driftData?.drifted ? "#D85A30" : "#1D9E75")} />
        </div>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(320px, 1fr))", gap: 10 }}>
        <div style={{ border: "0.5px solid var(--color-border-tertiary)", borderRadius: "var(--border-radius-lg)", padding: 12, background: "var(--color-background-secondary)" }}>
          <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 8 }}>Latest Model Run</div>
          {!latestRun ? (
            <div style={{ fontSize: 12, color: "var(--color-text-secondary)" }}>No model run records available.</div>
          ) : (
            <div style={{ display: "grid", gap: 6, fontSize: 12 }}>
              <div><strong>Model:</strong> {latestRun.model_name} {latestRun.model_version ? `(${latestRun.model_version})` : ""}</div>
              <div><strong>Trained:</strong> {latestRun.trained_at ? latestRun.trained_at.replace("T", " ").slice(0, 19) : "N/A"}</div>
              <div><strong>Rows:</strong> {latestRun.training_rows ?? "N/A"}</div>
              <div><strong>Target:</strong> {latestRun.target || "N/A"}</div>
              <div><strong>Limitations:</strong> {(latestRun.limitations || []).length ? latestRun.limitations.join(" | ") : "None reported"}</div>
            </div>
          )}
        </div>

        <div style={{ border: "0.5px solid var(--color-border-tertiary)", borderRadius: "var(--border-radius-lg)", padding: 12, background: "var(--color-background-secondary)" }}>
          <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 8 }}>Forecast Backtest</div>
          {accuracyError ? (
            <div style={{ fontSize: 12, color: "#D85A30" }}>Accuracy report unavailable: {accuracyError}</div>
          ) : (
            <div style={{ display: "grid", gridTemplateColumns: "repeat(2, minmax(0,1fr))", gap: 8, fontSize: 12 }}>
              <div><strong>Status:</strong> {backtest.status || "N/A"}</div>
              <div><strong>Folds:</strong> {backtest.folds ?? "N/A"}</div>
              <div><strong>MAE:</strong> {backtest.mae != null ? Number(backtest.mae).toFixed(2) : "N/A"}</div>
              <div><strong>RMSE:</strong> {backtest.rmse != null ? Number(backtest.rmse).toFixed(2) : "N/A"}</div>
              <div><strong>MAPE:</strong> {backtest.mape != null ? `${Number(backtest.mape).toFixed(2)}%` : "N/A"}</div>
              <div><strong>Bias:</strong> {backtest.bias != null ? Number(backtest.bias).toFixed(2) : "N/A"}</div>
            </div>
          )}
          {driftError ? (
            <div style={{ fontSize: 11, color: "var(--color-text-secondary)", marginTop: 8 }}>
              Drift details unavailable: {driftError}
            </div>
          ) : (
            <div style={{ fontSize: 11, color: "var(--color-text-secondary)", marginTop: 8 }}>
              Drifted metrics: {(driftData?.drifted_metrics || []).length ? driftData.drifted_metrics.join(", ") : "none"}
            </div>
          )}
        </div>
      </div>

      <div style={{ border: "0.5px solid var(--color-border-tertiary)", borderRadius: "var(--border-radius-lg)", padding: 12, background: "var(--color-background-primary)" }}>
        <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 8 }}>Predictions by Model</div>
        <div style={{ overflowX: "auto" }}>
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
            <thead>
              <tr style={{ color: "var(--color-text-secondary)", textTransform: "uppercase", letterSpacing: "0.4px", fontSize: 10 }}>
                <th style={{ textAlign: "left", padding: "6px 8px", fontWeight: 500 }}>Model</th>
                <th style={{ textAlign: "right", padding: "6px 8px", fontWeight: 500 }}>Predictions</th>
                <th style={{ textAlign: "left", padding: "6px 8px", fontWeight: 500 }}>Latest Prediction Time</th>
              </tr>
            </thead>
            <tbody>
              {Object.keys(countsByModel).length === 0 ? (
                <tr>
                  <td colSpan={3} style={{ padding: "9px 8px", color: "var(--color-text-secondary)" }}>No prediction summary available.</td>
                </tr>
              ) : Object.entries(countsByModel).map(([model, count]) => (
                <tr key={model} style={{ borderTop: "0.5px solid var(--color-border-tertiary)" }}>
                  <td style={{ padding: "7px 8px", fontWeight: 500 }}>{model}</td>
                  <td style={{ textAlign: "right", padding: "7px 8px" }}>{count}</td>
                  <td style={{ padding: "7px 8px", color: "var(--color-text-secondary)" }}>
                    {latestByModel?.[model]?.predicted_at ? String(latestByModel[model].predicted_at).replace("T", " ").slice(0, 19) : "N/A"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

function EnterpriseGradeTab({ refreshKey, activeCompany, userRole }) {
  const role = userRole || "executive";
  const company = activeCompany || "";
  const { data, loading, error } = useFetch(withRefresh("/grading/enterprise-readiness", refreshKey), { role, company });
  if (loading) return <Skeleton h={320} />;
  if (error) return <div style={{ color: "#D85A30", padding: 14 }}>Enterprise grade report unavailable: {error}</div>;

  const categories = data?.categories || [];
  const functionalChecks = data?.functional_checks || [];
  const criticalGaps = data?.critical_gaps || [];
  const recommendations = data?.recommendations || [];

  return (
    <div style={{ display: "grid", gap: 14 }}>
      <div style={{ border: "0.5px solid var(--color-border-tertiary)", borderRadius: "var(--border-radius-lg)", padding: 16, background: "linear-gradient(120deg, rgba(14,35,56,0.08) 0%, rgba(29,158,117,0.08) 100%)" }}>
        <div style={{ fontSize: 11, textTransform: "uppercase", letterSpacing: "0.6px", color: "var(--color-text-secondary)", marginBottom: 4 }}>Enterprise Grade</div>
        <div style={{ fontSize: 18, fontWeight: 600, marginBottom: 8 }}>Production Readiness Scorecard</div>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(170px, 1fr))", gap: 8 }}>
          <MetricCard label="Overall Score" value={String(data?.overall_score ?? "N/A")} sub="Out of 100" color="#378ADD" />
          <MetricCard label="Grade" value={String(data?.grade || "N/A")} sub="Readiness band" color={data?.grade === "A" ? "#1D9E75" : data?.grade === "B" ? "#378ADD" : "#EF9F27"} />
          <MetricCard label="Functional Pass" value={`${Number(data?.functional_pass_rate || 0).toFixed(1)}%`} sub={`${functionalChecks.filter((c) => c.passed).length}/${functionalChecks.length || 0} checks`} />
          <MetricCard label="Categories" value={String(categories.length)} sub="Weighted scoring buckets" />
        </div>
      </div>

      <div style={{ border: "0.5px solid var(--color-border-tertiary)", borderRadius: "var(--border-radius-lg)", padding: 12, background: "var(--color-background-primary)" }}>
        <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 8 }}>Category Breakdown</div>
        <div style={{ overflowX: "auto" }}>
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
            <thead>
              <tr style={{ color: "var(--color-text-secondary)", textTransform: "uppercase", letterSpacing: "0.4px", fontSize: 10 }}>
                <th style={{ textAlign: "left", padding: "6px 8px", fontWeight: 500 }}>Category</th>
                <th style={{ textAlign: "right", padding: "6px 8px", fontWeight: 500 }}>Score</th>
                <th style={{ textAlign: "right", padding: "6px 8px", fontWeight: 500 }}>Max</th>
                <th style={{ textAlign: "right", padding: "6px 8px", fontWeight: 500 }}>Coverage</th>
              </tr>
            </thead>
            <tbody>
              {categories.map((cat) => {
                const coverage = cat.max_score > 0 ? (Number(cat.score || 0) / Number(cat.max_score || 1)) * 100 : 0;
                return (
                  <tr key={cat.name} style={{ borderTop: "0.5px solid var(--color-border-tertiary)" }}>
                    <td style={{ padding: "7px 8px", fontWeight: 500 }}>{cat.name}</td>
                    <td style={{ textAlign: "right", padding: "7px 8px" }}>{cat.score}</td>
                    <td style={{ textAlign: "right", padding: "7px 8px" }}>{cat.max_score}</td>
                    <td style={{ textAlign: "right", padding: "7px 8px", color: coverage >= 90 ? "#1D9E75" : coverage >= 70 ? "#EF9F27" : "#D85A30" }}>{coverage.toFixed(1)}%</td>
                  </tr>
                );
              })}
              {categories.length === 0 && (
                <tr>
                  <td colSpan={4} style={{ padding: "9px 8px", color: "var(--color-text-secondary)" }}>No category data available.</td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(320px, 1fr))", gap: 10 }}>
        <div style={{ border: "0.5px solid var(--color-border-tertiary)", borderRadius: "var(--border-radius-lg)", padding: 12, background: "var(--color-background-secondary)" }}>
          <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 8 }}>Critical Gaps</div>
          {criticalGaps.length === 0 ? (
            <div style={{ fontSize: 12, color: "var(--color-text-secondary)" }}>No critical gaps reported.</div>
          ) : (
            <ul style={{ margin: 0, paddingLeft: 18, fontSize: 12, display: "grid", gap: 5 }}>
              {criticalGaps.map((gap, idx) => <li key={`${gap}-${idx}`}>{gap}</li>)}
            </ul>
          )}
        </div>

        <div style={{ border: "0.5px solid var(--color-border-tertiary)", borderRadius: "var(--border-radius-lg)", padding: 12, background: "var(--color-background-secondary)" }}>
          <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 8 }}>Recommendations</div>
          {recommendations.length === 0 ? (
            <div style={{ fontSize: 12, color: "var(--color-text-secondary)" }}>No recommendations returned.</div>
          ) : (
            <ul style={{ margin: 0, paddingLeft: 18, fontSize: 12, display: "grid", gap: 5 }}>
              {recommendations.map((item, idx) => <li key={`${item}-${idx}`}>{item}</li>)}
            </ul>
          )}
        </div>
      </div>
    </div>
  );
}

function ReportsTab() {
  const [reportType, setReportType] = useState("executive_weekly");
  const [periodType, setPeriodType] = useState("monthly");
  const [reportTypeOptions, setReportTypeOptions] = useState([]);
  const currentYear = new Date().getFullYear();
  const prevDate = new Date();
  prevDate.setMonth(prevDate.getMonth() - 1);
  const defaultMonth = String(prevDate.getMonth() + 1).padStart(2, "0");
  const defaultYear = String(prevDate.getFullYear());
  const [selectedYear, setSelectedYear] = useState(defaultYear);
  const [selectedMonth, setSelectedMonth] = useState(defaultMonth);
  const [loading, setLoading] = useState(false);
  const [report, setReport] = useState(null);
  const [error, setError] = useState(null);
  const [selectedDoc, setSelectedDoc] = useState(null);
  const [docContent, setDocContent] = useState("");
  const [docLoading, setDocLoading] = useState(false);
  const [docError, setDocError] = useState(null);

  useEffect(() => {
    fetch(`${API}/reports/types`)
      .then((r) => r.json())
      .then((d) => {
        if (d.report_types && d.labels) {
          setReportTypeOptions(d.report_types.map((rt) => ({ value: rt, label: d.labels[rt] || rt })));
          return;
        }
        if (d.report_types) {
          setReportTypeOptions(d.report_types.map((rt) => ({ value: rt, label: rt.replace(/_/g, " ") })));
        }
      })
      .catch(() => {
        setReportTypeOptions([
          { value: "executive_weekly", label: "Executive Weekly" },
          { value: "manager_monthly", label: "Manager Monthly" },
          { value: "rep_performance", label: "Rep Performance" },
          { value: "payout_statement", label: "Payout Statement" },
          { value: "forecast_summary", label: "Forecast Summary" },
        ]);
      });
  }, []);

  const period = periodType === "monthly" ? `${selectedYear}-${selectedMonth}` : selectedYear;
  const YEARS = Array.from({ length: 6 }, (_, i) => String(currentYear - 3 + i));
  const MONTHS = [
    ["01", "January"], ["02", "February"], ["03", "March"], ["04", "April"],
    ["05", "May"], ["06", "June"], ["07", "July"], ["08", "August"],
    ["09", "September"], ["10", "October"], ["11", "November"], ["12", "December"],
  ];
  const citationLabels = {
    summary: "Summary",
    key_metrics: "Key Metrics",
    what_changed: "What Changed",
    risks: "Risks",
    opportunities: "Opportunities",
    quality: "Data Quality",
  };

  const generateReport = async (nextReportType, nextPeriod) => {
    const rt = nextReportType || reportType;
    const p = nextPeriod || period;
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(`${API}/reports/generate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ report_type: rt, period: p, audience: "Sales Leadership", filters: {} }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Failed to generate report");
      setReport(data);
      setSelectedDoc(null);
      setDocContent("");
      setDocError(null);
    } catch (e) {
      setError(e.message || "Unable to generate report");
    }
    setLoading(false);
  };

  useEffect(() => {
    generateReport("executive_weekly", `${defaultYear}-${defaultMonth}`);
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const openKnowledgeDoc = async (source) => {
    if (!source || !String(source).endsWith(".md")) return;
    setSelectedDoc(source);
    setDocLoading(true);
    setDocError(null);
    try {
      const res = await fetch(`${API}/reports/knowledge-base/${encodeURIComponent(source)}`);
      if (!res.ok) throw new Error(`Failed to fetch ${source}`);
      const data = await res.json();
      setDocContent(data.content || "");
    } catch {
      setDocError("Unable to load knowledge document for this citation.");
      setDocContent("");
    }
    setDocLoading(false);
  };

  const evidenceSectionCount = report?.evidence_citations ? Object.keys(report.evidence_citations).length : 0;
  const warningCount = report?.warnings?.length || 0;
  const metricsUsedCount = report?.metrics_used ? Object.keys(report.metrics_used).length : 0;

  return (
    <div style={{ display: "grid", gap: 14 }}>
      <div style={{ border: "0.5px solid var(--color-border-tertiary)", borderRadius: "var(--border-radius-lg)", padding: 18, display: "grid", gap: 12, background: "linear-gradient(135deg, rgba(55,138,221,0.1), rgba(14,35,56,0.06))" }}>
        <div>
          <div style={{ fontSize: 11, textTransform: "uppercase", letterSpacing: "0.6px", color: "var(--color-text-secondary)", marginBottom: 3 }}>Report Studio</div>
          <div style={{ fontSize: 16, fontWeight: 600 }}>Narrative, Evidence, and Risk Signals</div>
        </div>

        <div style={{ display: "grid", gridTemplateColumns: "minmax(0, 1fr) auto", gap: 10, alignItems: "center" }}>
          <select value={reportType} onChange={(e) => setReportType(e.target.value)} style={{ padding: "9px 10px", borderRadius: "var(--border-radius-md)", border: "0.5px solid var(--color-border-secondary)", background: "var(--color-background-primary)", color: "var(--color-text-primary)", fontSize: 13 }}>
            {reportTypeOptions.map((opt) => <option key={opt.value} value={opt.value}>{opt.label}</option>)}
          </select>
          <button onClick={() => generateReport()} disabled={loading} style={{ padding: "9px 18px", borderRadius: "var(--border-radius-md)", border: "none", background: "#0E2338", color: "#F8FBFF", cursor: "pointer", fontWeight: 500 }}>
            {loading ? "Generating..." : "Generate Report"}
          </button>
        </div>

        <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
          <div style={{ display: "flex", border: "0.5px solid var(--color-border-secondary)", borderRadius: "var(--border-radius-md)", overflow: "hidden" }}>
            {["monthly", "yearly"].map((type) => (
              <button
                key={type}
                onClick={() => setPeriodType(type)}
                style={{
                  padding: "7px 14px",
                  fontSize: 12,
                  cursor: "pointer",
                  border: "none",
                  background: periodType === type ? "var(--color-text-primary)" : "var(--color-background-primary)",
                  color: periodType === type ? "var(--color-background-primary)" : "var(--color-text-secondary)",
                }}
              >
                {type}
              </button>
            ))}
          </div>
          <select value={selectedYear} onChange={(e) => setSelectedYear(e.target.value)} style={{ padding: "7px 10px", borderRadius: "var(--border-radius-md)", border: "0.5px solid var(--color-border-secondary)", background: "var(--color-background-primary)", fontSize: 13, color: "var(--color-text-primary)" }}>
            {YEARS.map((y) => <option key={y} value={y}>{y}</option>)}
          </select>
          {periodType === "monthly" && (
            <select value={selectedMonth} onChange={(e) => setSelectedMonth(e.target.value)} style={{ padding: "7px 10px", borderRadius: "var(--border-radius-md)", border: "0.5px solid var(--color-border-secondary)", background: "var(--color-background-primary)", fontSize: 13, color: "var(--color-text-primary)" }}>
              {MONTHS.map(([val, label]) => <option key={val} value={val}>{label}</option>)}
            </select>
          )}
          <span style={{ fontSize: 12, color: "var(--color-text-secondary)", border: "0.5px solid var(--color-border-secondary)", borderRadius: 999, padding: "4px 10px", background: "var(--color-background-primary)" }}>
            Period: <strong>{period}</strong>
          </span>
        </div>

        {error && <div style={{ fontSize: 12, color: "#D85A30" }}>{error}</div>}
      </div>

      {report && (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(170px, 1fr))", gap: 10 }}>
          <MetricCard label="Evidence Sections" value={String(evidenceSectionCount)} sub="Narrative coverage" />
          <MetricCard label="Warnings" value={String(warningCount)} sub="Data or model cautions" color={warningCount > 0 ? "#D85A30" : "#1D9E75"} />
          <MetricCard label="Metrics Referenced" value={String(metricsUsedCount)} sub="Quantitative inputs" color="#378ADD" />
          <MetricCard label="Generated" value={report.generated_at ? String(report.generated_at).slice(0, 10) : "-"} sub={report.report_type || reportType} />
        </div>
      )}

      <div style={{ border: "0.5px solid var(--color-border-tertiary)", borderRadius: "var(--border-radius-lg)", padding: 16, minHeight: 220, maxHeight: 500, overflowY: "auto", whiteSpace: "pre-wrap", fontSize: 13, lineHeight: 1.6, background: "var(--color-background-primary)" }}>
        {loading ? "Generating report..." : report?.markdown || "Generate a report to view markdown output."}
      </div>

      <div style={{ border: "0.5px solid var(--color-border-tertiary)", borderRadius: "var(--border-radius-lg)", padding: 16, background: "var(--color-background-primary)" }}>
        <div style={{ fontSize: 13, fontWeight: 500, marginBottom: 10 }}>Evidence Citations</div>
        {!report?.evidence_citations && <div style={{ fontSize: 12, color: "var(--color-text-secondary)" }}>Generate a report to view section citations.</div>}
        {report?.evidence_citations && (
          <div style={{ display: "grid", gap: 8 }}>
            {Object.entries(report.evidence_citations).map(([section, sources]) => (
              <div key={section} style={{ border: "0.5px solid var(--color-border-tertiary)", borderRadius: "var(--border-radius-md)", padding: 10, background: "var(--color-background-secondary)" }}>
                <div style={{ fontSize: 12, fontWeight: 500, marginBottom: 6 }}>{citationLabels[section] || section}</div>
                <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
                  {(sources || []).length > 0 ? (sources || []).map((source, idx) => (
                    <button
                      key={`${section}-${source}-${idx}`}
                      onClick={() => openKnowledgeDoc(source)}
                      style={{ border: "0.5px solid var(--color-border-secondary)", borderRadius: 999, padding: "4px 10px", background: "var(--color-background-primary)", fontSize: 11, cursor: "pointer" }}
                    >
                      {source}
                    </button>
                  )) : <span style={{ fontSize: 12, color: "var(--color-text-secondary)" }}>No citations</span>}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {selectedDoc && (
        <div style={{ border: "0.5px solid var(--color-border-tertiary)", borderRadius: "var(--border-radius-lg)", padding: 16, background: "var(--color-background-primary)" }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
            <div style={{ fontSize: 13, fontWeight: 500 }}>Knowledge Document: {selectedDoc}</div>
            <button onClick={() => setSelectedDoc(null)} style={{ fontSize: 11, border: "0.5px solid var(--color-border-secondary)", borderRadius: 6, background: "var(--color-background-secondary)", padding: "4px 8px", cursor: "pointer" }}>Close</button>
          </div>
          {docLoading && <div style={{ fontSize: 12, color: "var(--color-text-secondary)" }}>Loading document...</div>}
          {docError && <div style={{ fontSize: 12, color: "#D85A30" }}>{docError}</div>}
          {!docLoading && !docError && <div style={{ whiteSpace: "pre-wrap", fontSize: 12, color: "var(--color-text-primary)" }}>{docContent || "No content returned."}</div>}
        </div>
      )}
    </div>
  );
}

function IngestionTab({ refreshKey, activeCompany, onCompanyLoaded }) {
  // ── Upload state ───────────────────────────────────────────────────────
  const [files, setFiles] = useState([]);
  const [companyName, setCompanyName] = useState("");
  const [loadMode, setLoadMode] = useState("full_reload");
  const [resetDb, setResetDb] = useState(true);
  const [useManifest, setUseManifest] = useState(true);
  const [manifestName, setManifestName] = useState("sales_schema");
  const [manifestVersion, setManifestVersion] = useState("v1");
  const [uploading, setUploading] = useState(false);
  const [uploadResult, setUploadResult] = useState(null);
  const [uploadError, setUploadError] = useState("");
  const [dragOver, setDragOver] = useState(false);
  const fileInputRef = useRef(null);

  const ALLOWED_EXTS = [".csv", ".xlsx", ".xls", ".pdf"];
  const FILE_TYPE_COLORS = { csv: "#1D9E75", xlsx: "#217346", xls: "#217346", pdf: "#D85A30" };
  const getExt = (name) => name.split(".").pop().toLowerCase();

  const addFiles = (newFiles) => {
    const filtered = newFiles.filter(f => ALLOWED_EXTS.includes("." + getExt(f.name)));
    const invalid = newFiles.filter(f => !ALLOWED_EXTS.includes("." + getExt(f.name)));
    if (invalid.length) setUploadError(`Unsupported file type(s): ${invalid.map(f=>f.name).join(", ")}`);
    else setUploadError("");
    setFiles(prev => {
      const names = new Set(prev.map(f => f.name));
      return [...prev, ...filtered.filter(f => !names.has(f.name))];
    });
    setUploadResult(null);
  };

  const handleFilesChange = (e) => { addFiles(Array.from(e.target.files)); };

  const handleDrop = (e) => {
    e.preventDefault(); setDragOver(false);
    addFiles(Array.from(e.dataTransfer.files));
  };

  const removeFile = (name) => setFiles(prev => prev.filter(f => f.name !== name));

  const handleUpload = async () => {
    if (!files.length) { setUploadError("Select at least one CSV, Excel, or PDF file."); return; }
    if (!companyName.trim()) { setUploadError("Enter a company name."); return; }
    setUploading(true);
    setUploadResult(null);
    setUploadError("");
    try {
      const fd = new FormData();
      fd.append("company_name", companyName.trim());
      fd.append("reset_database", resetDb ? "true" : "false");
      fd.append("load_mode", loadMode);
      fd.append("use_manifest", useManifest ? "true" : "false");
      fd.append("manifest_name", manifestName.trim() || "sales_schema");
      fd.append("manifest_version", manifestVersion.trim() || "v1");
      files.forEach((f) => fd.append("files", f));
      const res = await fetch(`${API}/ingestion/upload-intelligent-load`, { method: "POST", body: fd });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(body.detail || `Upload failed (${res.status})`);
      setUploadResult(body);
      if (onCompanyLoaded) onCompanyLoaded(body.company_name);
    } catch (e) {
      setUploadError(e.message || "Upload failed.");
    }
    setUploading(false);
  };

  const qg = uploadResult?.quality_gate;
  const revops = uploadResult?.revops_validation;
  const statusColor = { ok: "#1D9E75", medium: "#E8A838", high: "#D85A30", critical: "#C0392B" };
  const revopsPassed = revops?.passed !== false;

  return (
    <div style={{ display: "grid", gap: 12 }}>

      {/* ── Upload Panel ── */}
      <div style={{ border: "0.5px solid var(--color-border-tertiary)", borderRadius: "var(--border-radius-lg)", padding: 16 }}>
        <div style={{ fontSize: 13, fontWeight: 500, marginBottom: 6 }}>Upload Company Data</div>
        <div style={{ fontSize: 12, color: "var(--color-text-secondary)", marginBottom: 14 }}>
          Upload CSV, Excel (.xlsx / .xls), or PDF files — columns are auto-mapped from Salesforce, HubSpot, and other CRM exports.
          The pipeline inspects, normalises, validates, and loads the data automatically.
        </div>

        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10, marginBottom: 10 }}>
          <div>
            <label style={{ fontSize: 11, color: "var(--color-text-secondary)", display: "block", marginBottom: 4 }}>Company Name *</label>
            <input
              value={companyName}
              onChange={(e) => setCompanyName(e.target.value)}
              placeholder="e.g. Acme Corp"
              style={{ width: "100%", padding: "6px 8px", fontSize: 12, borderRadius: "var(--border-radius-md)", border: "0.5px solid var(--color-border-secondary)", background: "var(--color-background-primary)", color: "var(--color-text-primary)", boxSizing: "border-box" }}
            />
          </div>
          <div>
            <label style={{ fontSize: 11, color: "var(--color-text-secondary)", display: "block", marginBottom: 4 }}>Load Mode</label>
            <select
              value={loadMode}
              onChange={(e) => setLoadMode(e.target.value)}
              style={{ width: "100%", padding: "6px 8px", fontSize: 12, borderRadius: "var(--border-radius-md)", border: "0.5px solid var(--color-border-secondary)", background: "var(--color-background-primary)", color: "var(--color-text-primary)" }}
            >
              <option value="full_reload">Full Reload — drop &amp; recreate</option>
              <option value="upsert">Upsert — update or insert</option>
              <option value="append">Append — new rows only</option>
            </select>
          </div>
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 10 }}>
          <input type="checkbox" id="resetDb" checked={resetDb} onChange={(e) => setResetDb(e.target.checked)} />
          <label htmlFor="resetDb" style={{ fontSize: 12, color: "var(--color-text-secondary)" }}>
            Reset database before load <span style={{ color: "#D85A30" }}>(only applies to full_reload)</span>
          </label>
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 10 }}>
          <input type="checkbox" id="useManifest" checked={useManifest} onChange={(e) => setUseManifest(e.target.checked)} />
          <label htmlFor="useManifest" style={{ fontSize: 12, color: "var(--color-text-secondary)" }}>
            Use manifest-guided ingestion mapping
          </label>
        </div>

        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10, marginBottom: 10 }}>
          <div>
            <label style={{ fontSize: 11, color: "var(--color-text-secondary)", display: "block", marginBottom: 4 }}>Manifest Name</label>
            <input
              value={manifestName}
              onChange={(e) => setManifestName(e.target.value)}
              disabled={!useManifest}
              style={{ width: "100%", padding: "6px 8px", fontSize: 12, borderRadius: "var(--border-radius-md)", border: "0.5px solid var(--color-border-secondary)", background: "var(--color-background-primary)", color: "var(--color-text-primary)", boxSizing: "border-box" }}
            />
          </div>
          <div>
            <label style={{ fontSize: 11, color: "var(--color-text-secondary)", display: "block", marginBottom: 4 }}>Manifest Version</label>
            <input
              value={manifestVersion}
              onChange={(e) => setManifestVersion(e.target.value)}
              disabled={!useManifest}
              style={{ width: "100%", padding: "6px 8px", fontSize: 12, borderRadius: "var(--border-radius-md)", border: "0.5px solid var(--color-border-secondary)", background: "var(--color-background-primary)", color: "var(--color-text-primary)", boxSizing: "border-box" }}
            />
          </div>
        </div>

        {/* Drag-and-drop zone */}
        <div
          onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
          onDragLeave={() => setDragOver(false)}
          onDrop={handleDrop}
          onClick={() => fileInputRef.current?.click()}
          style={{
            marginBottom: 12, border: `2px dashed ${dragOver ? "var(--color-text-primary)" : "var(--color-border-secondary)"}`,
            borderRadius: "var(--border-radius-md)", padding: "20px 16px", textAlign: "center",
            cursor: "pointer", background: dragOver ? "var(--color-background-secondary)" : "transparent",
            transition: "all 0.15s",
          }}
        >
          <div style={{ fontSize: 22, marginBottom: 6 }}>📂</div>
          <div style={{ fontSize: 12, color: "var(--color-text-secondary)" }}>
            Drag &amp; drop files here, or <span style={{ textDecoration: "underline" }}>click to browse</span>
          </div>
          <div style={{ fontSize: 11, color: "var(--color-text-secondary)", marginTop: 4 }}>
            Supported: <strong>CSV</strong>, <strong>Excel (.xlsx/.xls)</strong>, <strong>PDF</strong> · Salesforce, HubSpot &amp; generic CRM exports
          </div>
          <input
            ref={fileInputRef}
            type="file"
            multiple
            accept=".csv,.xlsx,.xls,.pdf"
            onChange={handleFilesChange}
            style={{ display: "none" }}
          />
        </div>

        {/* File list */}
        {files.length > 0 && (
          <div style={{ marginBottom: 12, display: "flex", flexWrap: "wrap", gap: 6 }}>
            {files.map((f) => {
              const ext = getExt(f.name);
              const color = FILE_TYPE_COLORS[ext] || "var(--color-text-secondary)";
              return (
                <span key={f.name} style={{ display: "inline-flex", alignItems: "center", gap: 4, background: "var(--color-background-secondary)", borderRadius: 4, padding: "3px 8px", fontSize: 11 }}>
                  <span style={{ color, fontWeight: 600, textTransform: "uppercase", fontSize: 10 }}>{ext}</span>
                  {f.name}
                  <span onClick={(e) => { e.stopPropagation(); removeFile(f.name); }} style={{ marginLeft: 2, cursor: "pointer", color: "#D85A30", fontWeight: 700 }}>×</span>
                </span>
              );
            })}
          </div>
        )}

        <button
          onClick={handleUpload}
          disabled={uploading}
          style={{ padding: "7px 18px", fontSize: 12, fontWeight: 500, borderRadius: "var(--border-radius-md)", border: "none", background: uploading ? "var(--color-border-secondary)" : "var(--color-text-primary)", color: "var(--color-background-primary)", cursor: uploading ? "not-allowed" : "pointer" }}
        >
          {uploading ? "Processing…" : "Upload & Ingest"}
        </button>

        {uploadError && <div style={{ marginTop: 10, fontSize: 12, color: "#D85A30" }}>{uploadError}</div>}

        {/* ── Result Summary ── */}
        {uploadResult && (
          <div style={{ marginTop: 14, padding: 12, background: "var(--color-background-secondary)", borderRadius: "var(--border-radius-md)", fontSize: 12 }}>
            <div style={{ fontWeight: 500, marginBottom: 8 }}>
              Ingestion complete — <span style={{ color: statusColor[qg?.overall_status] || "#1D9E75" }}>{(qg?.overall_status || "ok").toUpperCase()}</span>
              {" "}· confidence {Math.round((qg?.confidence ?? 1) * 100)}%
            </div>

            <div style={{ fontSize: 11, color: "var(--color-text-secondary)", marginBottom: 8 }}>
              Mode: <strong>{uploadResult.load_mode || loadMode}</strong> · Manifest: <strong>{uploadResult.use_manifest ? `${manifestName}:${manifestVersion}` : "disabled"}</strong>
            </div>

            {revops && (
              <div style={{ marginBottom: 10, border: "0.5px solid var(--color-border-tertiary)", borderRadius: "var(--border-radius-md)", padding: 10, background: "var(--color-background-primary)" }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 6 }}>
                  <div style={{ fontWeight: 500 }}>RevOps Validation</div>
                  <span style={{ fontSize: 11, padding: "2px 6px", borderRadius: 4, background: revopsPassed ? "#1D9E7522" : "#D85A3022", color: revopsPassed ? "#1D9E75" : "#D85A30" }}>
                    {revopsPassed ? "PASS" : "HARD FAIL"}
                  </span>
                </div>
                <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 6 }}>
                  <span style={{ background: "var(--color-background-secondary)", borderRadius: 4, padding: "2px 8px" }}>Hard Fails: <strong>{revops.hard_fail_count ?? 0}</strong></span>
                  <span style={{ background: "var(--color-background-secondary)", borderRadius: 4, padding: "2px 8px" }}>Warnings: <strong>{revops.warn_count ?? 0}</strong></span>
                </div>
                {(revops.violations || []).slice(0, 3).map((v, idx) => (
                  <div key={`violation-${idx}`} style={{ color: "#D85A30", marginBottom: 2 }}>
                    [HARD_FAIL] {v.rule}: {v.message}
                  </div>
                ))}
                {(revops.warnings || []).slice(0, 3).map((w, idx) => (
                  <div key={`warning-${idx}`} style={{ color: "#E8A838", marginBottom: 2 }}>
                    [WARN] {w.rule}: {w.message}
                  </div>
                ))}
              </div>
            )}

            {/* Rows loaded */}
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 8 }}>
              {Object.entries(uploadResult.db_rows_loaded || {}).map(([k, v]) => (
                <span key={k} style={{ background: "var(--color-background-primary)", border: "0.5px solid var(--color-border-tertiary)", borderRadius: 4, padding: "2px 8px" }}>
                  {k}: <strong>{v}</strong>
                </span>
              ))}
            </div>

            {/* Quality issues */}
            {(qg?.issues || []).length > 0 && (
              <div style={{ marginBottom: 8 }}>
                <div style={{ fontWeight: 500, marginBottom: 4 }}>Quality Issues</div>
                {qg.issues.map((iss, i) => (
                  <div key={i} style={{ color: statusColor[iss.severity] || "var(--color-text-secondary)", marginBottom: 2 }}>
                    [{iss.severity.toUpperCase()}] {iss.message}
                  </div>
                ))}
              </div>
            )}

            {/* Source manifest */}
            {(uploadResult.source_manifest || []).length > 0 && (
              <div>
                <div style={{ fontWeight: 500, marginBottom: 4 }}>Sources Processed</div>
                {uploadResult.source_manifest.map((s, i) => (
                  <div key={i} style={{ color: "var(--color-text-secondary)", marginBottom: 2 }}>
                    {s.file_name} — {s.source_type?.toUpperCase()} · {s.row_count ?? "?"} rows · entities: {(s.inferred_entities || [s.entity_type || "?"]).join(", ")}
                  </div>
                ))}
              </div>
            )}

            {/* Warnings */}
            {(uploadResult.warnings || []).length > 0 && (
              <details style={{ marginTop: 8 }}>
                <summary style={{ cursor: "pointer", color: "var(--color-text-secondary)" }}>{uploadResult.warnings.length} warning(s)</summary>
                {uploadResult.warnings.map((w, i) => <div key={i} style={{ color: "var(--color-text-secondary)", marginTop: 2 }}>{w}</div>)}
              </details>
            )}
          </div>
        )}
      </div>

    </div>
  );
}

// ── Root App ──────────────────────────────────────────────────────────────
// ── ARR Health Tab ────────────────────────────────────────────────────────
function ArrHealthTab({ refreshKey, period, userRole, activeCompany }) {
  const role = userRole || "executive";
  const company = activeCompany || "";
  const { data, loading, error } = useFetch(withRefresh("/analytics/revops-kpis", refreshKey), { role, company });
  const { data: waterfall, loading: wLoading } = useFetch(withRefresh("/ml/forecast/arr-waterfall", refreshKey), { role, company });

  if (loading) return <Skeleton h={300} />;
  if (error) return <div style={{ color: "#D85A30", padding: 16 }}>RevOps KPIs unavailable: {error}</div>;
  if (!data) return <div style={{ padding: 16 }}>No RevOps data available.</div>;

  const nrr = data.nrr_pct ?? 0;
  const grr = data.grr_pct ?? 0;
  const growth = data.arr_growth_pct ?? 0;

  const nrrColor = nrr >= 110 ? "#1D9E75" : nrr >= 100 ? "#EF9F27" : "#D85A30";
  const grrColor = grr >= 85 ? "#1D9E75" : grr >= 75 ? "#EF9F27" : "#D85A30";
  const growthColor = growth >= 20 ? "#1D9E75" : growth >= 10 ? "#EF9F27" : "#D85A30";

  const wRaw = waterfall?.waterfall || {};
  // Backend returns columnar arrays; convert to row-oriented for Recharts
  const wChartData = Array.isArray(wRaw)
    ? wRaw.map((row) => ({
        period: (row.period || "").slice(0, 7),
        new_logo: row.new_logo ?? 0,
        expansion: row.expansion ?? 0,
        contraction: -(row.contraction ?? 0),
        churn: -(row.churn ?? 0),
        renewal: row.renewal ?? 0,
      }))
    : (wRaw.periods || []).map((p, i) => ({
        period: (p || "").slice(0, 7),
        new_logo: (wRaw.new_logo?.[i] ?? 0),
        expansion: (wRaw.expansion?.[i] ?? 0),
        contraction: -(wRaw.contraction?.[i] ?? 0),
        churn: -(wRaw.churn?.[i] ?? 0),
        renewal: (wRaw.renewal?.[i] ?? 0),
      }));

  // Derived waterfall totals for summary
  const wTotals = wChartData.reduce(
    (acc, d) => ({
      new_logo: acc.new_logo + (d.new_logo || 0),
      expansion: acc.expansion + (d.expansion || 0),
      renewal: acc.renewal + (d.renewal || 0),
      churn: acc.churn + Math.abs(d.churn || 0),
      contraction: acc.contraction + Math.abs(d.contraction || 0),
    }),
    { new_logo: 0, expansion: 0, renewal: 0, churn: 0, contraction: 0 }
  );
  const wGross = wTotals.new_logo + wTotals.expansion + wTotals.renewal;
  const wLoss = wTotals.churn + wTotals.contraction;
  const wNetMotion = wGross - wLoss;
  const dominantDriver = wTotals.new_logo >= wTotals.expansion ? "new logo acquisition" : "expansion revenue";
  const churnRatio = wGross > 0 ? (wTotals.churn / wGross) * 100 : 0;

  return (
    <div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(4,1fr)", gap: 10, marginBottom: "1rem" }}>
        <MetricCard label="Net Revenue Retention" value={pct(nrr)} sub="Benchmark ≥ 110%" color={nrrColor} />
        <MetricCard label="Gross Revenue Retention" value={pct(grr)} sub="Benchmark ≥ 85%" color={grrColor} />
        <MetricCard label="ARR Growth (YoY)" value={pct(growth)} sub="Benchmark ≥ 20%" color={growthColor} />
        <MetricCard label="Avg Sales Cycle" value={`${(data.avg_sales_cycle_days ?? 0).toFixed(0)} days`} sub="Closed-won deals" />
      </div>

      {/* ARR Health Contextual Summary */}
      {(() => {
        const isHealthy = nrr >= 110 && grr >= 85;
        const nrrGap = 110 - nrr;
        const grrGap = 85 - grr;
        const bg = isHealthy ? "rgba(29,158,117,0.06)" : nrr < 100 ? "rgba(216,90,48,0.06)" : "rgba(239,159,39,0.06)";
        const border = isHealthy ? "#1D9E7530" : nrr < 100 ? "#D85A3030" : "#EF9F2730";
        const icon = isHealthy ? "✅" : nrr < 100 ? "🔴" : "⚠️";
        const headline = isHealthy
          ? "ARR health is strong — existing customers are expanding faster than they churn."
          : nrr < 100
            ? "ARR is contracting from the installed base. Churn and contraction exceed expansion revenue."
            : "ARR health is mixed. Retention is holding but not yet reaching best-in-class thresholds.";
        const bullets = [];
        if (nrrGap > 0) bullets.push(`NRR is ${nrrGap.toFixed(1)}pp below the 110% benchmark — expansion plays and upsell motions are the fastest lever.`);
        else bullets.push(`NRR at ${pct(nrr)} is above the 110% threshold — customers are growing their spend. Protect this through renewal discipline.`);
        if (grrGap > 0) bullets.push(`GRR is ${grrGap.toFixed(1)}pp below 85% — logo churn or downgrades are compressing the base. Prioritize at-risk account coverage.`);
        else bullets.push(`GRR at ${pct(grr)} is solid. Most customers are renewing at or above their original commitment.`);
        if (growth < 10) bullets.push(`YoY ARR growth of ${pct(growth)} is below 10% — consider activating new territories or verticals to accelerate the growth rate.`);
        else if (growth >= 20) bullets.push(`YoY ARR growth of ${pct(growth)} exceeds the 20% benchmark. Sustain by balancing new logo acquisition with expansion on top accounts.`);
        return (
          <div style={{ background: bg, border: `1px solid ${border}`, borderRadius: "var(--border-radius-md)", padding: "12px 16px", marginBottom: "1.25rem", fontSize: 12 }}>
            <div style={{ fontWeight: 600, marginBottom: 6, fontSize: 12.5 }}>{icon} {headline}</div>
            {bullets.map((b, i) => <div key={i} style={{ color: "var(--color-text-secondary)", marginTop: 4, paddingLeft: 10, borderLeft: "2px solid var(--color-border-secondary)", lineHeight: 1.5 }}>{b}</div>)}
          </div>
        );
      })()}

      {(data.warnings || []).length > 0 && (
        <div style={{ background: "#FFF8E7", border: "0.5px solid #EF9F27", borderRadius: "var(--border-radius-md)", padding: "10px 14px", marginBottom: "1rem", fontSize: 12 }}>
          {data.warnings.map((w, i) => <div key={i}>{w}</div>)}
        </div>
      )}

      <div style={{ border: "0.5px solid var(--color-border-tertiary)", borderRadius: "var(--border-radius-lg)", padding: 16, marginBottom: 16 }}>
        <div style={{ fontSize: 13, fontWeight: 500, marginBottom: 14 }}>ARR Waterfall</div>
        {wLoading ? <Skeleton /> : wChartData.length === 0 ? <div style={{ fontSize: 12, color: "var(--color-text-secondary)" }}>No waterfall data. Generate with archetype profiles for precise decomposition.</div> : (
          <>
            <ResponsiveContainer width="100%" height={220}>
              <BarChart data={wChartData}>
                <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border-tertiary)" />
                <XAxis dataKey="period" tick={{ fontSize: 10 }} />
                <YAxis tickFormatter={v => fmt(Math.abs(v))} tick={{ fontSize: 11 }} />
                <Tooltip formatter={(v, n) => [fmt(Math.abs(v)), n]} />
                <Legend />
                <Bar dataKey="new_logo" name="New Logo" stackId="a" fill="#1D9E75" />
                <Bar dataKey="expansion" name="Expansion" stackId="a" fill="#378ADD" />
                <Bar dataKey="renewal" name="Renewal" stackId="a" fill="#B5D4F4" />
                <Bar dataKey="contraction" name="Contraction" stackId="b" fill="#EF9F27" />
                <Bar dataKey="churn" name="Churn" stackId="b" fill="#D85A30" />
              </BarChart>
            </ResponsiveContainer>
            {/* Waterfall contextual summary */}
            <div style={{ marginTop: 12, padding: "10px 14px", borderRadius: "var(--border-radius-md)", background: "var(--color-background-secondary)", border: "0.5px solid var(--color-border-tertiary)", fontSize: 11, color: "var(--color-text-secondary)", display: "flex", flexWrap: "wrap", gap: "6px 20px", lineHeight: 1.6 }}>
              <span>ARR motion is primarily driven by <strong style={{ color: "var(--color-text-primary)" }}>{dominantDriver}</strong>.</span>
              <span>Gross inflows: <strong style={{ color: "#1D9E75" }}>{fmt(wGross)}</strong> · Gross outflows: <strong style={{ color: "#D85A30" }}>{fmt(wLoss)}</strong></span>
              <span>Net movement: <strong style={{ color: wNetMotion >= 0 ? "#1D9E75" : "#D85A30" }}>{wNetMotion >= 0 ? "+" : ""}{fmt(wNetMotion)}</strong></span>
              {churnRatio > 15 && <span style={{ color: "#D85A30" }}>⚠ Churn is consuming {churnRatio.toFixed(1)}% of gross inflows — above healthy threshold of 10–15%.</span>}
              {churnRatio <= 10 && wGross > 0 && <span style={{ color: "#1D9E75" }}>Churn rate of {churnRatio.toFixed(1)}% of gross inflows is within healthy range.</span>}
            </div>
          </>
        )}
      </div>

      <div style={{ border: "0.5px solid var(--color-border-tertiary)", borderRadius: "var(--border-radius-lg)", padding: 16 }}>
        <div style={{ fontSize: 13, fontWeight: 500, marginBottom: 10 }}>Quota Attainment Distribution</div>
        {(() => {
          const dist = data.attainment_distribution || {};
          const counts = dist.counts || {};
          const tiers = [
            { label: "Above 120%", key: "above_120", color: "#1D9E75", bench: "10–20%" },
            { label: "100–120%", key: "100_to_120", color: "#378ADD", bench: "25–35%" },
            { label: "75–100%", key: "75_to_100", color: "#B5D4F4", bench: "20–30%" },
            { label: "50–75%", key: "50_to_75", color: "#EF9F27", bench: "10–15%" },
            { label: "Below 50%", key: "below_50", color: "#D85A30", bench: "< 10%" },
          ];
          const chartData = tiers.map(t => ({ name: t.label, count: counts[t.key] ?? 0, fill: t.color }));
          const totalReps = chartData.reduce((s, d) => s + d.count, 0);
          const atOrAbove = (counts.above_120 ?? 0) + (counts["100_to_120"] ?? 0);
          const below50 = counts.below_50 ?? 0;
          const atOrAbovePct = totalReps > 0 ? (atOrAbove / totalReps) * 100 : 0;
          const below50Pct = totalReps > 0 ? (below50 / totalReps) * 100 : 0;
          return (
            <>
              <ResponsiveContainer width="100%" height={160}>
                <BarChart data={chartData} layout="vertical">
                  <XAxis type="number" tick={{ fontSize: 11 }} allowDecimals={false} />
                  <YAxis dataKey="name" type="category" tick={{ fontSize: 11 }} width={90} />
                  <Tooltip formatter={(v) => [`${v} reps`]} />
                  <Bar dataKey="count" radius={[0, 4, 4, 0]}>
                    {chartData.map((d, i) => <Cell key={i} fill={d.fill} />)}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
              {/* Attainment distribution contextual summary */}
              <div style={{ marginTop: 12, padding: "10px 14px", borderRadius: "var(--border-radius-md)", background: "var(--color-background-secondary)", border: "0.5px solid var(--color-border-tertiary)", fontSize: 11, color: "var(--color-text-secondary)", lineHeight: 1.6 }}>
                {totalReps > 0 ? (
                  <>
                    <span style={{ fontWeight: 600, color: "var(--color-text-primary)", fontSize: 11.5 }}>
                      {atOrAbovePct >= 50 ? "✅ Majority of reps are at or above quota." : below50Pct > 20 ? "🔴 Significant portion of the team is well below quota." : "⚠️ Attainment distribution shows room to improve."}
                    </span>
                    <div style={{ marginTop: 6, display: "flex", flexWrap: "wrap", gap: "4px 18px" }}>
                      <span><strong style={{ color: "#1D9E75" }}>{atOrAbove}</strong> of {totalReps} reps ({atOrAbovePct.toFixed(0)}%) are at or above 100% quota.</span>
                      {below50 > 0 && <span><strong style={{ color: "#D85A30" }}>{below50}</strong> reps ({below50Pct.toFixed(0)}%) are below 50% — flag for coaching or resource review.</span>}
                      {atOrAbovePct < 40 && <span style={{ color: "#D85A30" }}>Fewer than 40% of reps are meeting quota — evaluate quota calibration and enablement coverage.</span>}
                      {atOrAbovePct >= 60 && <span style={{ color: "#1D9E75" }}>Strong quota attainment breadth — quotas may have headroom to increase next cycle.</span>}
                    </div>
                  </>
                ) : <span>No attainment data available for this period.</span>}
              </div>
            </>
          );
        })()}
      </div>
    </div>
  );
}

// ── Pipeline Health Tab ───────────────────────────────────────────────────
function PipelineHealthTab({ refreshKey, period, userRole, activeCompany }) {
  const role = userRole || "executive";
  const company = activeCompany || "";
  const kpisUrl = withPeriod(withRefresh("/analytics/kpis", refreshKey), period);
  const { data: revops, loading } = useFetch(withRefresh("/analytics/revops-kpis", refreshKey), { role, company });
  const { data: kpis, loading: kLoading } = useFetch(kpisUrl, { role, company });
  const { data: slipData, loading: sLoading } = useFetch(withRefresh("/ml/score/deal-slip", refreshKey), { role, company });

  if (loading || kLoading) return <Skeleton h={300} />;

  const weighted = revops?.weighted_pipeline_coverage ?? 0;
  const openPipeline = kpis?.open_pipeline ?? 0;
  const quota = kpis?.total_quota ?? 1;
  const rawCoverage = quota > 0 ? openPipeline / quota : 0;
  const activity = revops?.activity_ratio ?? 0;

  const rawColor = rawCoverage >= 4 ? "#1D9E75" : rawCoverage >= 2.5 ? "#EF9F27" : "#D85A30";
  const weightedColor = weighted >= 3 ? "#1D9E75" : weighted >= 2 ? "#EF9F27" : "#D85A30";
  const activityColor = activity >= 3 ? "#1D9E75" : activity >= 2 ? "#EF9F27" : "#D85A30";

  const topSlips = (slipData?.at_risk_deals || []).slice(0, 10);

  return (
    <div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(4,1fr)", gap: 10, marginBottom: "1.5rem" }}>
        <MetricCard label="Raw Coverage" value={`${rawCoverage.toFixed(2)}×`} sub="Benchmark ≥ 4×" color={rawColor} />
        <MetricCard label="Weighted Coverage" value={`${Number(weighted).toFixed(2)}×`} sub="Benchmark ≥ 3×" color={weightedColor} />
        <MetricCard label="Activity Ratio" value={`${Number(activity).toFixed(1)}`} sub="Activities per deal" color={activityColor} />
        <MetricCard label="Open Pipeline" value={fmt(openPipeline)} sub={`vs ${fmt(quota)} quota`} />
      </div>

      <div style={{ border: "0.5px solid var(--color-border-tertiary)", borderRadius: "var(--border-radius-lg)", padding: 16, marginBottom: 16 }}>
        <div style={{ fontSize: 13, fontWeight: 500, marginBottom: 10 }}>Top Deal Slip Risks</div>
        {sLoading ? <Skeleton h={120} /> : topSlips.length === 0 ? (
          <div style={{ fontSize: 12, color: "var(--color-text-secondary)" }}>No at-risk deals identified.</div>
        ) : (
          <table style={{ width: "100%", fontSize: 12, borderCollapse: "collapse" }}>
              <thead>
                <tr style={{ color: "var(--color-text-secondary)", borderBottom: "0.5px solid var(--color-border-tertiary)" }}>
                  <th style={{ textAlign: "left", padding: "4px 8px" }}>Deal ID</th>
                  <th style={{ textAlign: "right", padding: "4px 8px" }}>Amount</th>
                  <th style={{ textAlign: "right", padding: "4px 8px" }}>Slip Risk</th>
                  <th style={{ textAlign: "left", padding: "4px 8px" }}>Stage</th>
                </tr>
              </thead>
              <tbody>
                {topSlips.map((d, i) => (
                  <tr key={i} style={{ borderBottom: "0.5px solid var(--color-border-tertiary)" }}>
                    <td style={{ padding: "4px 8px" }}>{d.deal_id}</td>
                    <td style={{ padding: "4px 8px", textAlign: "right" }}>{fmt(d.amount ?? 0)}</td>
                    <td style={{ padding: "4px 8px", textAlign: "right", color: d.slip_score > 0.7 ? "#D85A30" : "#EF9F27" }}>{pct((d.slip_score ?? 0) * 100)}</td>
                    <td style={{ padding: "4px 8px" }}>{d.stage || "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
        )}
      </div>

      <div style={{ border: "0.5px solid var(--color-border-tertiary)", borderRadius: "var(--border-radius-lg)", padding: 16 }}>
        <div style={{ fontSize: 13, fontWeight: 500, marginBottom: 6 }}>Coverage Assessment</div>
        <div style={{ fontSize: 12, color: "var(--color-text-secondary)", lineHeight: 1.6 }}>
          {rawCoverage < 4 ? "⚠️ Raw coverage below 4× — increase top-of-funnel activity." : "✅ Raw coverage is healthy."}<br />
          {weighted < 3 ? "⚠️ Weighted coverage below 3× — improve stage quality and engagement." : "✅ Weighted coverage is healthy."}<br />
          {activity < 3 ? "⚠️ Activity ratio below 3 — boost deal engagement." : "✅ Engagement levels are adequate."}
        </div>
      </div>
    </div>
  );
}

function RevOpsControlCenterTab({ refreshKey, activeCompany, period, userRole }) {
  const role = userRole || "executive";
  const company = activeCompany || "";
  const kpisUrl = withPeriod(withRefresh("/analytics/kpis", refreshKey), period);
  const revopsUrl = withPeriod(withRefresh("/analytics/revops-kpis", refreshKey), period);
  const payoutsUrl = withPeriod(withRefresh("/analytics/payouts", refreshKey), period);
  const orgUrl = withPeriod(withRefresh("/analytics/org-structure", refreshKey), period);
  const clustersUrl = withPeriod(withRefresh("/ml/cluster/reps", refreshKey), period);
  const { data: kpis, loading: kLoading, error: kError } = useFetch(kpisUrl, { role, company });
  const { data: revops, loading: rLoading, error: rError } = useFetch(revopsUrl, { role, company });
  const { data: payouts, loading: pLoading, error: pError } = useFetch(payoutsUrl, { role, company });
  const { data: quality, loading: qLoading } = useFetch(withRefresh("/data-quality/summary", refreshKey), { role, company });
  const { data: orgData, loading: oLoading } = useFetch(orgUrl, { role, company });
  const { data: plansGovernance, loading: gLoading, error: gError } = useFetch(withRefresh("/analytics/plans-governance", refreshKey), { role, company });
  const { data: clusters, loading: cLoading } = useFetch(clustersUrl, { role, company });
  const leadershipScopedUrl = withPeriod(withRefresh("/analytics/reps/leadership", refreshKey), period);
  const leadershipBaseUrl = withRefresh("/analytics/reps/leadership", refreshKey);
  const { data: leadershipScopedRaw, loading: lScopedLoading } = useFetch(leadershipScopedUrl, { role, company });
  const { data: leadershipBaseRaw, loading: lBaseLoading } = useFetch(leadershipBaseUrl, { role, company });
  const scopedLeadership = Array.isArray(leadershipScopedRaw) ? leadershipScopedRaw : (leadershipScopedRaw?.leaders || []);
  const baseLeadership = Array.isArray(leadershipBaseRaw) ? leadershipBaseRaw : (leadershipBaseRaw?.leaders || []);
  const leadership = scopedLeadership.length > 0 ? scopedLeadership : baseLeadership;

  const [selectedRepId, setSelectedRepId] = useState(null);
  const [territoryFilter, setTerritoryFilter] = useState("all");
  const [teamFilter, setTeamFilter] = useState("all");
  const [confidenceFilter, setConfidenceFilter] = useState("all");
  const [personaFilter, setPersonaFilter] = useState("all");
  const [searchText, setSearchText] = useState("");
  const [sortKey, setSortKey] = useState("attainment_pct");
  const [sortDir, setSortDir] = useState("desc");
  const [expandedTerritories, setExpandedTerritories] = useState({});
  const [repPage, setRepPage] = useState(1);
  const [repPageSize, setRepPageSize] = useState(10);
  const [rollupPage, setRollupPage] = useState(1);
  const [rollupPageSize, setRollupPageSize] = useState(4);

  const clusterByRep = {};
  (clusters?.clusters || []).forEach((row) => {
    const features = row.features || {};
    clusterByRep[row.rep_id] = {
      ...row,
      activity_rate: row.activity_rate ?? features.activity_rate ?? null,
      pipeline_coverage: row.pipeline_coverage ?? features.pipeline_coverage ?? null,
      attainment_pct: row.attainment_pct ?? features.attainment_pct ?? null,
      win_rate: row.win_rate ?? features.win_rate ?? null,
      avg_deal_size: row.avg_deal_size ?? features.avg_deal_size ?? null,
    };
  });

  const repRows = (payouts?.rows || []).map((row) => {
    const cluster = clusterByRep[row.rep_id] || {};
    return {
      ...row,
      attainment_pct: Number(row.attainment_pct ?? cluster.attainment_pct ?? 0),
      win_rate: Number(row.win_rate ?? cluster.win_rate ?? 0),
      persona: cluster.persona || row.persona || "Unclassified",
      activity_rate: cluster.activity_rate ?? row.activity_rate ?? null,
      pipeline_coverage: cluster.pipeline_coverage ?? row.pipeline_coverage ?? null,
      avg_deal_size: cluster.avg_deal_size ?? row.avg_deal_size ?? null,
    };
  });

  const repById = {};
  repRows.forEach((row) => {
    repById[row.rep_id] = row;
  });

  const fallbackTerritoryMap = {};
  repRows.forEach((row) => {
    const territory = row.region || "Unassigned";
    const teamName = row.team_name || row.region || "Default Team";
    if (!fallbackTerritoryMap[territory]) {
      fallbackTerritoryMap[territory] = {
        territory,
        teams: [],
      };
    }
    let team = fallbackTerritoryMap[territory].teams.find((t) => t.team_name === teamName);
    if (!team) {
      team = { team_name: teamName, members: [] };
      fallbackTerritoryMap[territory].teams.push(team);
    }
    team.members.push(row);
  });

  const fallbackTerritories = Object.values(fallbackTerritoryMap).map((territory) => {
    const teams = (territory.teams || []).map((team) => {
      const members = team.members || [];
      const revenue = members.reduce((sum, rep) => sum + Number(rep.revenue || 0), 0);
      const quota = members.reduce((sum, rep) => sum + Number(rep.quota || 0), 0);
      return {
        ...team,
        members,
        member_count: members.length,
        revenue,
        quota,
        attainment_pct: quota > 0 ? (revenue / quota) * 100 : 0,
      };
    });
    const revenue = teams.reduce((sum, team) => sum + Number(team.revenue || 0), 0);
    const quota = teams.reduce((sum, team) => sum + Number(team.quota || 0), 0);
    return {
      ...territory,
      teams,
      team_count: teams.length,
      member_count: teams.reduce((sum, team) => sum + Number(team.member_count || 0), 0),
      revenue,
      quota,
      attainment_pct: quota > 0 ? (revenue / quota) * 100 : 0,
    };
  });

  const orgTerritories = (orgData?.territories || []).map((territory) => {
    const teams = (territory.teams || []).map((team) => {
      const members = (team.members || []).map((member) => {
        const merged = repById[member.rep_id] || {};
        const cluster = clusterByRep[member.rep_id] || {};
        return {
          ...member,
          ...merged,
          persona: cluster.persona || merged.persona || "Unclassified",
          activity_rate: cluster.activity_rate ?? merged.activity_rate ?? null,
        };
      });
      const revenue = members.reduce((sum, rep) => sum + Number(rep.revenue || 0), 0);
      const quota = members.reduce((sum, rep) => sum + Number(rep.quota || 0), 0);
      return {
        ...team,
        members,
        member_count: team.member_count || members.length,
        revenue,
        quota,
        attainment_pct: quota > 0 ? (revenue / quota) * 100 : 0,
      };
    });
    const revenue = teams.reduce((sum, team) => sum + Number(team.revenue || 0), 0);
    const quota = teams.reduce((sum, team) => sum + Number(team.quota || 0), 0);
    return {
      ...territory,
      teams,
      team_count: territory.team_count || teams.length,
      member_count: territory.member_count || teams.reduce((sum, team) => sum + Number(team.member_count || 0), 0),
      revenue,
      quota,
      attainment_pct: quota > 0 ? (revenue / quota) * 100 : 0,
    };
  });

  const territories = orgTerritories.length > 0 ? orgTerritories : fallbackTerritories;

  const territoryMembersById = {};
  territories.forEach((territory) => {
    (territory.teams || []).forEach((team) => {
      (team.members || []).forEach((member) => {
        if (!territoryMembersById[member.rep_id]) {
          territoryMembersById[member.rep_id] = member;
        }
      });
    });
  });

  const territoryByRep = {};
  const teamsByRep = {};
  territories.forEach((territory) => {
    (territory.teams || []).forEach((team) => {
      (team.members || []).forEach((member) => {
        territoryByRep[member.rep_id] = territory.territory;
        if (!teamsByRep[member.rep_id]) teamsByRep[member.rep_id] = new Set();
        teamsByRep[member.rep_id].add(team.team_name);
      });
    });
  });

  const territoryOptions = territories.map((t) => t.territory);
  const teamOptions = Array.from(
    new Set(
      territories
        .filter((t) => territoryFilter === "all" || t.territory === territoryFilter)
        .flatMap((t) => (t.teams || []).map((team) => team.team_name))
    )
  ).sort();

  const filteredRepRows = repRows
    .filter((row) => {
      const territory = territoryByRep[row.rep_id] || row.region || "Unassigned";
      if (territoryFilter !== "all" && territory !== territoryFilter) return false;
      if (teamFilter !== "all" && !(teamsByRep[row.rep_id] || new Set()).has(teamFilter)) return false;
      if (confidenceFilter !== "all" && (row.confidence || "low") !== confidenceFilter) return false;
      if (personaFilter !== "all" && (row.persona || "Unclassified") !== personaFilter) return false;
      if (searchText.trim()) {
        const needle = searchText.trim().toLowerCase();
        const hay = `${row.name || ""} ${row.email || ""} ${territory}`.toLowerCase();
        if (!hay.includes(needle)) return false;
      }
      return true;
    })
    .sort((a, b) => {
      const direction = sortDir === "asc" ? 1 : -1;
      const av = Number(a[sortKey] || 0);
      const bv = Number(b[sortKey] || 0);
      if (av === bv) return (a.name || "").localeCompare(b.name || "");
      return av > bv ? direction : -direction;
    });

  useEffect(() => {
    setRepPage(1);
  }, [territoryFilter, teamFilter, confidenceFilter, personaFilter, searchText, sortKey, sortDir, repPageSize]);

  useEffect(() => {
    if (!selectedRepId || !filteredRepRows.some((row) => row.rep_id === selectedRepId)) {
      setSelectedRepId(filteredRepRows[0]?.rep_id || null);
    }
  }, [filteredRepRows, selectedRepId]);

  const repPageCount = Math.max(1, Math.ceil(filteredRepRows.length / Math.max(1, repPageSize)));
  const safeRepPage = Math.min(repPage, repPageCount);
  const pagedRepRows = filteredRepRows.slice((safeRepPage - 1) * repPageSize, safeRepPage * repPageSize);

  const filteredTerritories = territories
    .filter((territory) => territoryFilter === "all" || territory.territory === territoryFilter)
    .map((territory) => {
      const teams = (territory.teams || [])
        .filter((team) => teamFilter === "all" || team.team_name === teamFilter)
        .map((team) => {
          const members = (team.members || []).filter((member) => {
            const rep = repById[member.rep_id] || member;
            if (confidenceFilter !== "all" && (rep.confidence || "low") !== confidenceFilter) return false;
            if (personaFilter !== "all" && (rep.persona || "Unclassified") !== personaFilter) return false;
            if (searchText.trim()) {
              const needle = searchText.trim().toLowerCase();
              const hay = `${rep.name || ""} ${rep.email || ""}`.toLowerCase();
              if (!hay.includes(needle)) return false;
            }
            return true;
          });
          const revenue = members.reduce((sum, rep) => sum + Number(rep.revenue || 0), 0);
          const quota = members.reduce((sum, rep) => sum + Number(rep.quota || 0), 0);
          return {
            ...team,
            members,
            member_count: members.length,
            revenue,
            quota,
            attainment_pct: quota > 0 ? (revenue / quota) * 100 : 0,
          };
        })
        .filter((team) => (team.members || []).length > 0);

      const revenue = teams.reduce((sum, team) => sum + Number(team.revenue || 0), 0);
      const quota = teams.reduce((sum, team) => sum + Number(team.quota || 0), 0);
      return {
        ...territory,
        teams,
        team_count: teams.length,
        member_count: teams.reduce((sum, team) => sum + Number(team.member_count || 0), 0),
        revenue,
        quota,
        attainment_pct: quota > 0 ? (revenue / quota) * 100 : 0,
      };
    })
    .filter((territory) => (territory.teams || []).length > 0);

  useEffect(() => {
    setRollupPage(1);
  }, [territoryFilter, teamFilter, confidenceFilter, personaFilter, searchText, rollupPageSize]);

  const rollupPageCount = Math.max(1, Math.ceil(filteredTerritories.length / Math.max(1, rollupPageSize)));
  const safeRollupPage = Math.min(rollupPage, rollupPageCount);
  const pagedTerritories = filteredTerritories.slice((safeRollupPage - 1) * rollupPageSize, safeRollupPage * rollupPageSize);

  const payoutSummary = payouts?.summary || {};
  const governanceSummary = plansGovernance?.summary || {};
  const topGovernancePlans = (plansGovernance?.plans || []).slice(0, 4);
  const unassignedUsers = (plansGovernance?.unassigned_users || []).slice(0, 8);
  const uniqueRules = Array.from(new Set([
    ...repRows.flatMap((row) => row.rules_applied || []),
    ...(plansGovernance?.plans || []).flatMap((plan) => (plan.rules || []).map((rule) => rule.name)).filter(Boolean),
  ])).slice(0, 16);
  const payoutAccuracyPct = repRows.length > 0 ? (repRows.filter((row) => row.confidence === "high").length / repRows.length) * 100 : 0;
  const topAttainers = [...filteredRepRows]
    .sort((a, b) => Number(b.attainment_pct || 0) - Number(a.attainment_pct || 0))
    .slice(0, 3);
  const watchList = [...filteredRepRows]
    .filter((row) => row.fallback_used || row.confidence === "low" || Number(row.attainment_pct || 0) < 80)
    .sort((a, b) => Number(a.attainment_pct || 0) - Number(b.attainment_pct || 0))
    .slice(0, 3);
  const selectedRep = selectedRepId ? (repById[selectedRepId] || territoryMembersById[selectedRepId] || null) : null;

  const loading = kLoading || rLoading || pLoading || qLoading || oLoading || gLoading || cLoading || lScopedLoading || lBaseLoading;
  const error = kError || rError || pError;

  if (loading) return <Skeleton h={420} />;
  if (error) return <div style={{ color: "#D85A30", padding: 14 }}>RevOps control center unavailable: {error}</div>;

  return (
    <div style={{ display: "grid", gap: 16 }}>
      <div style={{ borderRadius: "var(--border-radius-lg)", padding: 18, border: "0.5px solid rgba(255,255,255,0.12)", background: "linear-gradient(126deg, #0E2338 0%, #1A4568 54%, #2A6A93 100%)", color: "#F8FBFF", boxShadow: "0 5px 18px rgba(14,35,56,0.2)" }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 16, flexWrap: "wrap", marginBottom: 12 }}>
          <div>
            <div style={{ fontSize: 11, letterSpacing: "0.6px", textTransform: "uppercase", opacity: 0.82, marginBottom: 6 }}>RevOps Control Center</div>
            <div style={{ fontSize: 20, fontWeight: 600, marginBottom: 4 }}>{activeCompany || "Company"} Operational Command</div>
            <div style={{ fontSize: 12, opacity: 0.88, maxWidth: 680 }}>Connected workspace for attainment, payout quality, plan governance, and rep-level action tracking.</div>
          </div>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
            <span style={{ fontSize: 11, padding: "3px 8px", borderRadius: 999, background: "#1D9E7533", color: "#8EE7C6" }}>Data quality: {quality?.status || "n/a"}</span>
            <span style={{ fontSize: 11, padding: "3px 8px", borderRadius: 999, background: "#378ADD33", color: "#A8CFF4" }}>KPI confidence: {kpis?.confidence || "n/a"}</span>
            <span style={{ fontSize: 11, padding: "3px 8px", borderRadius: 999, background: "#EF9F2733", color: "#FFD59D" }}>Weighted coverage: {Number(revops?.weighted_pipeline_coverage || 0).toFixed(2)}x</span>
          </div>
        </div>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))", gap: 8 }}>
          <div style={{ background: "rgba(248,251,255,0.12)", borderRadius: "var(--border-radius-md)", padding: "10px 12px" }}>
            <div style={{ fontSize: 10, textTransform: "uppercase", letterSpacing: "0.4px", opacity: 0.8 }}>Revenue</div>
            <div style={{ fontSize: 18, fontWeight: 600 }}>{fmt(Number(kpis?.total_revenue || 0))}</div>
          </div>
          <div style={{ background: "rgba(248,251,255,0.12)", borderRadius: "var(--border-radius-md)", padding: "10px 12px" }}>
            <div style={{ fontSize: 10, textTransform: "uppercase", letterSpacing: "0.4px", opacity: 0.8 }}>Attainment</div>
            <div style={{ fontSize: 18, fontWeight: 600 }}>{pct(Number(kpis?.attainment_pct || 0))}</div>
          </div>
          <div style={{ background: "rgba(248,251,255,0.12)", borderRadius: "var(--border-radius-md)", padding: "10px 12px" }}>
            <div style={{ fontSize: 10, textTransform: "uppercase", letterSpacing: "0.4px", opacity: 0.8 }}>Open pipeline</div>
            <div style={{ fontSize: 18, fontWeight: 600 }}>{fmt(Number(kpis?.open_pipeline || 0))}</div>
          </div>
          <div style={{ background: "rgba(248,251,255,0.12)", borderRadius: "var(--border-radius-md)", padding: "10px 12px" }}>
            <div style={{ fontSize: 10, textTransform: "uppercase", letterSpacing: "0.4px", opacity: 0.8 }}>Payout accuracy</div>
            <div style={{ fontSize: 18, fontWeight: 600 }}>{pct(payoutAccuracyPct)}</div>
          </div>
        </div>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(300px, 1fr))", gap: 10 }}>
        <div style={{ border: "0.5px solid var(--color-border-tertiary)", borderRadius: "var(--border-radius-lg)", padding: 12, background: "var(--color-background-secondary)" }}>
          <div style={{ fontSize: 11, textTransform: "uppercase", letterSpacing: "0.4px", color: "var(--color-text-secondary)", marginBottom: 6 }}>Top performers</div>
          <div style={{ display: "grid", gap: 6 }}>
            {topAttainers.length > 0 ? topAttainers.map((row) => (
              <div key={`top-${row.rep_id}`} style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8, border: "0.5px solid var(--color-border-tertiary)", borderRadius: "var(--border-radius-sm)", padding: "7px 8px", background: "var(--color-background-primary)" }}>
                <div>
                  <div style={{ fontSize: 12, fontWeight: 500 }}>{row.name}</div>
                  <div style={{ fontSize: 10, color: "var(--color-text-secondary)" }}>{row.persona || "Unclassified"}</div>
                </div>
                <div style={{ textAlign: "right" }}>
                  <div style={{ fontSize: 12, fontWeight: 600, color: "#1D9E75" }}>{pct(Number(row.attainment_pct || 0))}</div>
                  <div style={{ fontSize: 10, color: "var(--color-text-secondary)" }}>{fmt(Number(row.revenue || 0))}</div>
                </div>
              </div>
            )) : <div style={{ fontSize: 12, color: "var(--color-text-secondary)" }}>No reps available for the current filter.</div>}
          </div>
        </div>

        <div style={{ border: "0.5px solid var(--color-border-tertiary)", borderRadius: "var(--border-radius-lg)", padding: 12, background: "var(--color-background-secondary)" }}>
          <div style={{ fontSize: 11, textTransform: "uppercase", letterSpacing: "0.4px", color: "var(--color-text-secondary)", marginBottom: 6 }}>Watch list</div>
          <div style={{ display: "grid", gap: 6 }}>
            {watchList.length > 0 ? watchList.map((row) => (
              <div key={`risk-${row.rep_id}`} style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8, border: "0.5px solid var(--color-border-tertiary)", borderRadius: "var(--border-radius-sm)", padding: "7px 8px", background: "var(--color-background-primary)" }}>
                <div>
                  <div style={{ fontSize: 12, fontWeight: 500 }}>{row.name}</div>
                  <div style={{ fontSize: 10, color: "var(--color-text-secondary)" }}>{row.fallback_used ? "Fallback payout" : `${row.confidence || "low"} confidence`}</div>
                </div>
                <div style={{ textAlign: "right" }}>
                  <div style={{ fontSize: 12, fontWeight: 600, color: Number(row.attainment_pct || 0) < 80 ? "#D85A30" : "#EF9F27" }}>{pct(Number(row.attainment_pct || 0))}</div>
                  <div style={{ fontSize: 10, color: "var(--color-text-secondary)" }}>{fmt(Number(row.payout || 0))}</div>
                </div>
              </div>
            )) : <div style={{ fontSize: 12, color: "var(--color-text-secondary)" }}>No at-risk reps under current filters.</div>}
          </div>
        </div>
      </div>

      <div style={{ border: "0.5px solid var(--color-border-tertiary)", borderRadius: "var(--border-radius-lg)", padding: 12, display: "grid", gap: 10, background: "var(--color-background-secondary)" }}>
        <div style={{ fontSize: 12, color: "var(--color-text-secondary)", textTransform: "uppercase", letterSpacing: "0.5px" }}>Explore controls</div>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))", gap: 8 }}>
          <input value={searchText} onChange={(e) => setSearchText(e.target.value)} placeholder="Search rep name, email, territory..." style={{ padding: "7px 10px", borderRadius: "var(--border-radius-md)", border: "0.5px solid var(--color-border-secondary)", fontSize: 12, color: "var(--color-text-primary)", background: "var(--color-background-primary)" }} />
          <select value={territoryFilter} onChange={(e) => { setTerritoryFilter(e.target.value); setTeamFilter("all"); }} style={{ padding: "7px 10px", borderRadius: "var(--border-radius-md)", border: "0.5px solid var(--color-border-secondary)", fontSize: 12, color: "var(--color-text-primary)", background: "var(--color-background-primary)" }}>
            <option value="all">All territories</option>
            {territoryOptions.map((territory) => <option key={territory} value={territory}>{territory}</option>)}
          </select>
          <select value={teamFilter} onChange={(e) => setTeamFilter(e.target.value)} style={{ padding: "7px 10px", borderRadius: "var(--border-radius-md)", border: "0.5px solid var(--color-border-secondary)", fontSize: 12, color: "var(--color-text-primary)", background: "var(--color-background-primary)" }}>
            <option value="all">All teams</option>
            {teamOptions.map((team) => <option key={team} value={team}>{team}</option>)}
          </select>
          <select value={confidenceFilter} onChange={(e) => setConfidenceFilter(e.target.value)} style={{ padding: "7px 10px", borderRadius: "var(--border-radius-md)", border: "0.5px solid var(--color-border-secondary)", fontSize: 12, color: "var(--color-text-primary)", background: "var(--color-background-primary)" }}>
            <option value="all">All confidence</option>
            <option value="high">High</option>
            <option value="medium">Medium</option>
            <option value="low">Low</option>
          </select>
          <select value={personaFilter} onChange={(e) => setPersonaFilter(e.target.value)} style={{ padding: "7px 10px", borderRadius: "var(--border-radius-md)", border: "0.5px solid var(--color-border-secondary)", fontSize: 12, color: "var(--color-text-primary)", background: "var(--color-background-primary)" }}>
            <option value="all">All personas</option>
            {Array.from(new Set(repRows.map((row) => row.persona || "Unclassified"))).sort().map((persona) => <option key={persona} value={persona}>{persona}</option>)}
          </select>
          <button
            onClick={() => {
              setTerritoryFilter("all");
              setTeamFilter("all");
              setConfidenceFilter("all");
              setPersonaFilter("all");
              setSearchText("");
            }}
            style={{ padding: "7px 10px", borderRadius: "var(--border-radius-md)", border: "0.5px solid var(--color-border-secondary)", background: "var(--color-background-primary)", fontSize: 12, cursor: "pointer", color: "var(--color-text-primary)" }}
          >
            Reset
          </button>
        </div>
      </div>

      <div style={{ border: "0.5px solid var(--color-border-tertiary)", borderRadius: "var(--border-radius-lg)", padding: 14, background: "var(--color-background-primary)" }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 10, marginBottom: 10, flexWrap: "wrap" }}>
          <div>
            <div style={{ fontSize: 13, fontWeight: 600 }}>Rep performance + Selected Rep 360 workspace</div>
            <div style={{ fontSize: 11, color: "var(--color-text-secondary)" }}>A horizontal workspace that keeps ranking and deep rep context together for coaching decisions.</div>
          </div>
          <div style={{ display: "flex", gap: 8 }}>
            <select value={sortKey} onChange={(e) => setSortKey(e.target.value)} style={{ padding: "6px 8px", borderRadius: "var(--border-radius-md)", border: "0.5px solid var(--color-border-secondary)", fontSize: 11 }}>
              <option value="attainment_pct">Sort: attainment</option>
              <option value="revenue">Sort: revenue</option>
              <option value="payout">Sort: payout</option>
              <option value="activity_rate">Sort: activity</option>
              <option value="pipeline_coverage">Sort: pipeline</option>
            </select>
            <select value={sortDir} onChange={(e) => setSortDir(e.target.value)} style={{ padding: "6px 8px", borderRadius: "var(--border-radius-md)", border: "0.5px solid var(--color-border-secondary)", fontSize: 11 }}>
              <option value="desc">High to low</option>
              <option value="asc">Low to high</option>
            </select>
          </div>
        </div>

        <div style={{ display: "grid", gridTemplateColumns: "1fr", gap: 12, alignItems: "start" }}>
          {/* Rep table */}
          <div style={{ border: "0.5px solid var(--color-border-tertiary)", borderRadius: "var(--border-radius-md)", padding: 10, background: "var(--color-background-secondary)" }}>
            <div style={{ maxHeight: 360, overflowY: "auto" }}>
              <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
                <thead>
                  <tr style={{ borderBottom: "0.5px solid var(--color-border-tertiary)", color: "var(--color-text-secondary)", textTransform: "uppercase", letterSpacing: "0.4px", fontSize: 10 }}>
                    <th style={{ textAlign: "left", padding: "6px 8px", fontWeight: 500 }}>Rep</th>
                    <th style={{ textAlign: "right", padding: "6px 8px", fontWeight: 500 }}>Attainment</th>
                    <th style={{ textAlign: "right", padding: "6px 8px", fontWeight: 500 }}>Revenue</th>
                    <th style={{ textAlign: "right", padding: "6px 8px", fontWeight: 500 }}>Pipeline</th>
                    <th style={{ textAlign: "right", padding: "6px 8px", fontWeight: 500 }}>Activity</th>
                    <th style={{ textAlign: "right", padding: "6px 8px", fontWeight: 500 }}>Payout</th>
                  </tr>
                </thead>
                <tbody>
                  {pagedRepRows.map((row) => {
                    const confidenceBg = row.confidence === "high" ? "#1D9E7522" : row.confidence === "medium" ? "#EF9F2722" : "#D85A3022";
                    const confidenceFg = row.confidence === "high" ? "#1D9E75" : row.confidence === "medium" ? "#EF9F27" : "#D85A30";
                    return (
                      <tr key={row.rep_id} onClick={() => setSelectedRepId(row.rep_id)} style={{ borderBottom: "0.5px solid var(--color-border-tertiary)", cursor: "pointer", background: selectedRepId === row.rep_id ? "#F4F9FF" : "transparent" }}>
                        <td style={{ padding: "7px 8px" }}>
                          <div style={{ fontWeight: 500 }}>{row.name}</div>
                          <div style={{ fontSize: 10, color: "var(--color-text-secondary)", display: "flex", gap: 5, alignItems: "center" }}>
                            <span>{territoryByRep[row.rep_id] || row.region || "Unassigned"}</span>
                            <span style={{ borderRadius: 999, padding: "1px 6px", background: confidenceBg, color: confidenceFg }}>{row.confidence || "low"}</span>
                          </div>
                        </td>
                        <td style={{ padding: "7px 8px", textAlign: "right", fontWeight: 600, color: Number(row.attainment_pct || 0) >= 100 ? "#1D9E75" : "#D85A30" }}>{pct(Number(row.attainment_pct || 0))}</td>
                        <td style={{ padding: "7px 8px", textAlign: "right" }}>{fmt(Number(row.revenue || 0))}</td>
                        <td style={{ padding: "7px 8px", textAlign: "right" }}>{row.pipeline_coverage != null ? `${Number(row.pipeline_coverage).toFixed(2)}x` : "-"}</td>
                        <td style={{ padding: "7px 8px", textAlign: "right" }}>{row.activity_rate != null ? Number(row.activity_rate).toFixed(0) : "-"}</td>
                        <td style={{ padding: "7px 8px", textAlign: "right" }}>{fmt(Number(row.payout || 0))}</td>
                      </tr>
                    );
                  })}
                  {pagedRepRows.length === 0 && (
                    <tr>
                      <td colSpan={6} style={{ padding: "10px 8px", color: "var(--color-text-secondary)", fontSize: 12 }}>No reps match current filters.</td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
            <PaginationControls
              page={safeRepPage}
              pageSize={repPageSize}
              totalItems={filteredRepRows.length}
              onPageChange={setRepPage}
              onPageSizeChange={(next) => {
                setRepPageSize(next);
                setRepPage(1);
              }}
            />
          </div>

          {/* Selected Rep 360 — full-width below the table */}
          {selectedRep && (
            <div style={{ border: "1px solid var(--color-border-secondary)", borderRadius: "var(--border-radius-lg)", padding: 18, background: "var(--color-background-primary)", boxShadow: "var(--shadow-sm)" }}>
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 14 }}>
                <div style={{ fontSize: 13, fontWeight: 700 }}>Selected Rep 360</div>
                <button
                  onClick={() => setSelectedRepId(null)}
                  style={{ padding: "3px 10px", fontSize: 11, border: "1px solid var(--color-border-secondary)", borderRadius: "var(--border-radius-sm)", background: "var(--color-background-secondary)", cursor: "pointer", color: "var(--color-text-secondary)" }}
                >
                  ✕ Deselect
                </button>
              </div>
              <div style={{ display: "grid", gridTemplateColumns: "repeat(4, minmax(0, 1fr))", gap: 10, marginBottom: 14 }}>
                <div style={{ border: "0.5px solid var(--color-border-tertiary)", borderRadius: 8, padding: "8px 12px", background: "var(--color-background-secondary)" }}>
                  <div style={{ fontSize: 10, color: "var(--color-text-secondary)", textTransform: "uppercase", marginBottom: 4 }}>Attainment</div>
                  <div style={{ fontSize: 15, fontWeight: 700, color: Number(selectedRep.attainment_pct || 0) >= 100 ? "var(--color-green)" : "var(--color-red)" }}>{pct(Number(selectedRep.attainment_pct || 0))}</div>
                </div>
                <div style={{ border: "0.5px solid var(--color-border-tertiary)", borderRadius: 8, padding: "8px 12px", background: "var(--color-background-secondary)" }}>
                  <div style={{ fontSize: 10, color: "var(--color-text-secondary)", textTransform: "uppercase", marginBottom: 4 }}>Win Rate</div>
                  <div style={{ fontSize: 15, fontWeight: 700 }}>{pct(Number(selectedRep.win_rate || 0))}</div>
                </div>
                <div style={{ border: "0.5px solid var(--color-border-tertiary)", borderRadius: 8, padding: "8px 12px", background: "var(--color-background-secondary)" }}>
                  <div style={{ fontSize: 10, color: "var(--color-text-secondary)", textTransform: "uppercase", marginBottom: 4 }}>Pipeline</div>
                  <div style={{ fontSize: 15, fontWeight: 700 }}>{selectedRep.pipeline_coverage != null ? `${Number(selectedRep.pipeline_coverage).toFixed(2)}x` : "—"}</div>
                </div>
                <div style={{ border: "0.5px solid var(--color-border-tertiary)", borderRadius: 8, padding: "8px 12px", background: "var(--color-background-secondary)" }}>
                  <div style={{ fontSize: 10, color: "var(--color-text-secondary)", textTransform: "uppercase", marginBottom: 4 }}>Payout</div>
                  <div style={{ fontSize: 15, fontWeight: 700, color: "var(--color-green)" }}>{fmt(Number(selectedRep.payout || 0))}</div>
                </div>
              </div>
              <RepProfilePanel repId={selectedRepId} />
            </div>
          )}
          {!selectedRep && (
            <div style={{ padding: "20px 18px", border: "1px dashed var(--color-border-secondary)", borderRadius: "var(--border-radius-lg)", color: "var(--color-text-tertiary)", fontSize: 13, textAlign: "center" }}>
              Select a rep from the table above to view their detailed 360° profile.
            </div>
          )}
        </div>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "minmax(360px, 1fr) minmax(360px, 1fr)", gap: 12 }}>
        <div style={{ border: "0.5px solid var(--color-border-tertiary)", borderRadius: "var(--border-radius-lg)", padding: 14, background: "var(--color-background-primary)" }}>
          <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 10 }}>Territories and teams</div>
          <div style={{ display: "grid", gap: 8 }}>
            {pagedTerritories.map((territory) => (
              <div key={territory.territory} style={{ border: "0.5px solid var(--color-border-tertiary)", borderRadius: "var(--border-radius-md)", padding: 10, background: "var(--color-background-secondary)" }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 6, cursor: "pointer" }} onClick={() => setExpandedTerritories((prev) => ({ ...prev, [territory.territory]: !prev[territory.territory] }))}>
                  <div style={{ fontSize: 13, fontWeight: 500 }}>{expandedTerritories[territory.territory] ? "v" : ">"} {territory.territory}</div>
                  <div style={{ fontSize: 11, color: "var(--color-text-secondary)" }}>{territory.member_count || 0} reps, {territory.team_count || 0} teams</div>
                </div>
                <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 6, fontSize: 11, marginBottom: 6 }}>
                  <div><div style={{ color: "var(--color-text-secondary)" }}>Revenue</div><div style={{ fontWeight: 500 }}>{fmt(Number(territory.revenue || 0))}</div></div>
                  <div><div style={{ color: "var(--color-text-secondary)" }}>Quota</div><div style={{ fontWeight: 500 }}>{fmt(Number(territory.quota || 0))}</div></div>
                  <div><div style={{ color: "var(--color-text-secondary)" }}>Attainment</div><div style={{ fontWeight: 500 }}>{pct(Number(territory.attainment_pct || 0))}</div></div>
                </div>
                {expandedTerritories[territory.territory] && (
                  <div style={{ display: "grid", gap: 6 }}>
                    {(territory.teams || []).map((team) => (
                      <div key={`${territory.territory}-${team.team_name}`} style={{ border: "0.5px solid var(--color-border-tertiary)", borderRadius: "var(--border-radius-sm)", padding: 6, background: "var(--color-background-primary)" }}>
                        <div style={{ fontSize: 11, fontWeight: 500, marginBottom: 4 }}>{team.team_name} - {(team.members || []).length} members</div>
                        <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
                          {(team.members || []).map((member) => (
                            <button
                              key={member.rep_id}
                              onClick={() => setSelectedRepId(member.rep_id)}
                              style={{ border: "0.5px solid var(--color-border-secondary)", borderRadius: 999, background: selectedRepId === member.rep_id ? "#E6F1FB" : "var(--color-background-primary)", fontSize: 11, padding: "3px 8px", cursor: "pointer" }}
                            >
                              {member.name || member.rep_id}
                            </button>
                          ))}
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            ))}
            {pagedTerritories.length === 0 && <div style={{ fontSize: 12, color: "var(--color-text-secondary)" }}>No territory-level data for current filters.</div>}
          </div>
          <PaginationControls
            page={safeRollupPage}
            pageSize={rollupPageSize}
            totalItems={filteredTerritories.length}
            onPageChange={setRollupPage}
            onPageSizeChange={(next) => {
              setRollupPageSize(next);
              setRollupPage(1);
            }}
            pageSizeOptions={[2, 4, 6, 10]}
          />
        </div>

        <div style={{ border: "0.5px solid var(--color-border-tertiary)", borderRadius: "var(--border-radius-lg)", padding: 14, background: "var(--color-background-primary)" }}>
          <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 10 }}>Compensation governance and rule traceability</div>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(160px, 1fr))", gap: 8, marginBottom: 10 }}>
            <MetricCard label="Fallback payouts" value={String(payoutSummary.fallback_count || 0)} sub="Rows using fallback" color={(payoutSummary.fallback_count || 0) === 0 ? "#1D9E75" : "#EF9F27"} />
            <MetricCard label="Low confidence" value={String(payoutSummary.low_confidence_count || 0)} sub="Needs review" color={(payoutSummary.low_confidence_count || 0) === 0 ? "#1D9E75" : "#D85A30"} />
            <MetricCard label="Plan coverage" value={pct(Number(governanceSummary.assignment_coverage_pct || 0))} sub={`${governanceSummary.assigned_user_count || 0} assigned users`} />
            <MetricCard label="Rule footprint" value={String(uniqueRules.length)} sub="Distinct rules in traces" />
          </div>
          {gError && <div style={{ fontSize: 12, color: "#D85A30", marginBottom: 8 }}>Plans governance data unavailable: {gError}</div>}

          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))", gap: 10 }}>
            <div style={{ border: "0.5px solid var(--color-border-tertiary)", borderRadius: "var(--border-radius-md)", padding: 10, background: "var(--color-background-secondary)" }}>
              <div style={{ fontSize: 11, color: "var(--color-text-secondary)", marginBottom: 6, textTransform: "uppercase", letterSpacing: "0.4px" }}>Top plans and rules</div>
              <div style={{ display: "grid", gap: 6 }}>
                {topGovernancePlans.length > 0 ? topGovernancePlans.map((plan) => (
                  <div key={plan.plan_id} style={{ border: "0.5px solid var(--color-border-tertiary)", borderRadius: "var(--border-radius-sm)", padding: 8, background: "var(--color-background-primary)" }}>
                    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 4 }}>
                      <div style={{ fontSize: 12, fontWeight: 500 }}>{plan.name}</div>
                      <div style={{ fontSize: 10, color: "var(--color-text-secondary)" }}>{plan.assigned_user_count} assigned</div>
                    </div>
                    <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
                      {(plan.rules || []).slice(0, 3).map((rule) => (
                        <span key={rule.rule_id} style={{ fontSize: 10, border: "0.5px solid var(--color-border-secondary)", borderRadius: 999, padding: "2px 6px", background: "var(--color-background-secondary)" }}>
                          {rule.name}: {Number(rule.rate || 0) * 100}%
                        </span>
                      ))}
                    </div>
                  </div>
                )) : <div style={{ fontSize: 12, color: "var(--color-text-secondary)" }}>No plan governance data available.</div>}
              </div>
            </div>

            <div style={{ border: "0.5px solid var(--color-border-tertiary)", borderRadius: "var(--border-radius-md)", padding: 10, background: "var(--color-background-secondary)" }}>
              <div style={{ fontSize: 11, color: "var(--color-text-secondary)", marginBottom: 6, textTransform: "uppercase", letterSpacing: "0.4px" }}>Unassigned members</div>
              <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginBottom: 10 }}>
                {unassignedUsers.length > 0 ? unassignedUsers.map((user) => (
                  <span key={user.user_id} style={{ fontSize: 11, border: "0.5px solid var(--color-border-secondary)", borderRadius: 999, padding: "3px 8px", background: "var(--color-background-primary)" }}>
                    {user.name}
                  </span>
                )) : <span style={{ fontSize: 12, color: "var(--color-text-secondary)" }}>All users are covered by plans.</span>}
              </div>
              <div style={{ fontSize: 11, color: "var(--color-text-secondary)", marginBottom: 6, textTransform: "uppercase", letterSpacing: "0.4px" }}>Rules in active traces</div>
              <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
                {uniqueRules.length > 0 ? uniqueRules.map((rule) => (
                  <span key={rule} style={{ fontSize: 11, border: "0.5px solid var(--color-border-secondary)", borderRadius: 999, padding: "3px 8px", background: "var(--color-background-primary)" }}>
                    {rule}
                  </span>
                )) : <span style={{ fontSize: 12, color: "var(--color-text-secondary)" }}>No rule trace data available.</span>}
              </div>
            </div>
          </div>
        </div>
      </div>

      <LeadershipRollupPanel leadership={leadership} />
    </div>
  );
}

const NAV_MODULES = [
  { label: "Executive Overview", tabs: ["Dashboard", "RevOps Control Center", "Reports"] },
  { label: "Revenue Intelligence", tabs: ["Forecast", "ARR Health", "ARR Waterfall", "Pipeline Health"] },
  { label: "People & Territory", tabs: ["Reps", "Rep Scorecard", "Org Hierarchy", "Territories"] },
  { label: "Compensation", tabs: ["Payouts", "Payout Approvals", "Plans"] },
  { label: "AI & Operations", tabs: ["AI Agent", "Data Quality", "Model Monitoring", "Enterprise Grade", "ML Insights"] },
  { label: "Data Operations", tabs: ["Ingestion"] },
];

const ALL_TABS = NAV_MODULES.flatMap((module) => module.tabs);
const PERIOD_AWARE_TABS = new Set(["Dashboard", "RevOps Control Center", "ARR Health", "Pipeline Health", "Forecast", "Reps", "Territories", "Payouts"]);
const ROLE_TAB_ACCESS = {
  executive: new Set(ALL_TABS.filter((t) => !["Data Quality", "Model Monitoring", "Enterprise Grade", "Ingestion"].includes(t))),
  revops_admin: new Set(ALL_TABS),
  finance_admin: new Set(["Dashboard", "RevOps Control Center", "Payouts", "Payout Approvals", "Plans", "Reports", "AI Agent", "Data Quality"]),
  sales_manager: new Set(["Dashboard", "Forecast", "ARR Health", "Pipeline Health", "Reps", "Rep Scorecard", "Reports", "AI Agent"]),
  sales_rep: new Set(["Dashboard", "Rep Scorecard", "Forecast", "AI Agent"]),
  data_scientist: new Set(["Forecast", "ML Insights", "Model Monitoring", "Data Quality", "Reports", "AI Agent"]),
  auditor: new Set(["Dashboard", "Payouts", "Payout Approvals", "Reports", "Data Quality", "Model Monitoring", "Enterprise Grade"]),
};

export default function App() {
  // Tab, period and role live in the URL so a view can be linked, shared and
  // restored on reload, and so the back button undoes a navigation instead of
  // leaving the app. Company is deliberately not among them: switching company
  // triggers a load, so it is driven by the selector and its own effect below.
  const [view, setView] = useUrlState({
    tab: "Dashboard",
    period: "this quarter",
    role: "executive",
  });
  const tab = view.tab;
  const setTab = useCallback((next) => setView({ tab: next }), [setView]);
  const period = view.period;
  const setPeriod = useCallback((next) => setView({ period: next }), [setView]);
  const userRole = view.role;
  const setUserRole = useCallback((next) => setView({ role: next }), [setView]);

  const [activeCompany, setActiveCompany] = useState("");
  const [selectedCompany, setSelectedCompany] = useState("");
  const [companyLoadMsg, setCompanyLoadMsg] = useState("");
  const [companyLoadError, setCompanyLoadError] = useState("");
  const [switchingCompany, setSwitchingCompany] = useState(false);
  const [refreshKey, setRefreshKey] = useState(0);

  // Published during render rather than in an effect: React renders the parent
  // before its children, so every tab's first fetch already carries the current
  // company and role instead of missing them on the initial pass.
  setRequestContext({ role: userRole, company: activeCompany });

  const allowedTabs = ROLE_TAB_ACCESS[userRole] || new Set(ALL_TABS);
  const visibleTabs = ALL_TABS.filter((tabName) => allowedTabs.has(tabName));
  const visibleModules = useMemo(
    () => NAV_MODULES
      .map((module) => ({
        ...module,
        tabs: module.tabs.filter((moduleTab) => visibleTabs.includes(moduleTab)),
      }))
      .filter((module) => module.tabs.length > 0),
    [visibleTabs],
  );
  const activeModule = useMemo(
    () => visibleModules.find((module) => module.tabs.includes(tab)) || visibleModules[0] || null,
    [visibleModules, tab],
  );
  const roleBadgeText = (ROLES.find((r) => r.value === userRole)?.label || userRole).toUpperCase();
  const roleBadgeColor = (
    userRole === "revops_admin" || userRole === "finance_admin"
      ? { bg: "#FEE2E2", fg: "#B91C1C" }
      : userRole === "data_scientist"
        ? { bg: "#DBEAFE", fg: "#1D4ED8" }
        : { bg: "#DCFCE7", fg: "#166534" }
  );

  // When role changes, reset to first visible tab if current tab is hidden
  useEffect(() => {
    if (!visibleTabs.includes(tab)) {
      setTab(visibleTabs[0]);
    }
  }, [userRole, tab, visibleTabs]);
  const { data: companyData, loading: companiesLoading } = useFetch(withRefresh("/ingestion/companies", refreshKey));

  const loadCompany = useCallback(async (companyName) => {
    if (!companyName) return false;
    setSwitchingCompany(true);
    setCompanyLoadError("");
    setCompanyLoadMsg("");
    try {
      const res = await fetch(`${API}/ingestion/load-company`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...(userRole ? { "X-User-Role": userRole } : {}),
        },
        body: JSON.stringify({ company_name: companyName }),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) {
        throw new Error(body.detail || `Failed to load company (${res.status})`);
      }
      setActiveCompany(companyName);
      setSelectedCompany(companyName);
      setCompanyLoadMsg(`Loaded ${companyName} into dashboard context.`);
      setRefreshKey((v) => v + 1);
      return true;
    } catch (e) {
      setCompanyLoadError(e.message || "Failed to load company dataset.");
      return false;
    } finally {
      setSwitchingCompany(false);
    }
  }, [userRole]);

  useEffect(() => {
    const companies = companyData?.companies || [];
    if (!activeCompany && companies.length > 0) {
      const first = companies[0].name;
      setSelectedCompany(first);
      loadCompany(first);
    }
  }, [companyData, activeCompany, loadCompany]);

  const handleCompanyChange = async (event) => {
    const nextCompany = event.target.value;
    const previousLoadedCompany = activeCompany;
    setSelectedCompany(nextCompany);
    const ok = await loadCompany(nextCompany);
    if (!ok) {
      setSelectedCompany(previousLoadedCompany);
    }
  };

  return (
    <div style={{ maxWidth: 1200, margin: "0 auto", padding: "1.25rem 1.25rem 3rem", fontFamily: "var(--font-sans)", color: "var(--color-text-primary)" }}>

      {/* ── App Shell Header ─────────────────────────────────────────────── */}
      <div style={{
        border: "1px solid var(--color-border-secondary)",
        borderRadius: "var(--border-radius-xl)",
        padding: "16px 20px 0",
        background: "var(--color-background-primary)",
        boxShadow: "var(--shadow-md)",
        marginBottom: "1.25rem",
      }}>
        {/* Top row: brand + controls */}
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 16, flexWrap: "wrap", marginBottom: 14 }}>

          {/* Brand */}
          <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
            <div style={{
              width: 36, height: 36, borderRadius: 10,
              background: "linear-gradient(135deg, var(--color-blue) 0%, var(--color-green) 100%)",
              display: "flex", alignItems: "center", justifyContent: "center",
              fontSize: 16, color: "#fff", fontWeight: 700, flexShrink: 0,
              boxShadow: "0 2px 8px rgba(59,130,246,0.3)",
            }}>S</div>
            <div>
              <div style={{ fontSize: 17, fontWeight: 700, letterSpacing: "-0.4px", color: "var(--color-text-primary)", lineHeight: 1.2 }}>
                Sales Analytics <span style={{ color: "var(--color-blue)" }}>AI</span>
              </div>
              <div style={{ fontSize: 11, color: "var(--color-text-tertiary)", marginTop: 2, fontWeight: 500 }}>
                FastAPI · PostgreSQL · ML Ensemble · AI Agent
              </div>
            </div>
          </div>

          {/* Right controls */}
          <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>

            {/* Company selector */}
            <div style={{ display: "flex", alignItems: "center", gap: 7 }}>
              <span style={{ fontSize: 11, fontWeight: 600, color: "var(--color-text-secondary)", textTransform: "uppercase", letterSpacing: "0.5px" }}>Company</span>
              <select
                value={selectedCompany}
                onChange={handleCompanyChange}
                disabled={companiesLoading || switchingCompany || (companyData?.companies || []).length === 0}
                style={{ padding: "6px 10px", borderRadius: "var(--border-radius-md)", border: "1px solid var(--color-border-secondary)", background: "var(--color-background-secondary)", fontSize: 12, fontWeight: 500, color: "var(--color-text-primary)", minWidth: 140 }}
              >
                {(companyData?.companies || []).map((c) => (
                  <option key={c.name} value={c.name}>{c.name}</option>
                ))}
              </select>
              {switchingCompany && (
                <span style={{ fontSize: 11, color: "var(--color-text-tertiary)", display: "flex", alignItems: "center", gap: 4 }}>
                  <span style={{ display: "inline-block", width: 10, height: 10, border: "2px solid var(--color-blue)", borderTopColor: "transparent", borderRadius: "50%", animation: "spin 0.8s linear infinite" }} />
                  Loading…
                </span>
              )}
            </div>

            {/* Divider */}
            <div style={{ width: 1, height: 24, background: "var(--color-border-secondary)" }} />

            {/* Period selector */}
            {PERIOD_AWARE_TABS.has(tab) && (
              <div style={{ display: "flex", alignItems: "center", gap: 7 }}>
                <span style={{ fontSize: 11, fontWeight: 600, color: "var(--color-text-secondary)", textTransform: "uppercase", letterSpacing: "0.5px" }}>Period</span>
                <select
                  value={period}
                  onChange={e => setPeriod(e.target.value)}
                  style={{ padding: "6px 10px", borderRadius: "var(--border-radius-md)", border: "1px solid var(--color-border-secondary)", background: "var(--color-background-secondary)", fontSize: 12, fontWeight: 500, color: "var(--color-text-primary)" }}
                >
                  {PERIOD_OPTIONS.map(p => <option key={p} value={p}>{p}</option>)}
                </select>
              </div>
            )}

            {/* Divider */}
            <div style={{ width: 1, height: 24, background: "var(--color-border-secondary)" }} />

            {/* Role switcher */}
            <div style={{ display: "flex", alignItems: "center", gap: 7 }}>
              <span style={{ fontSize: 11, fontWeight: 600, color: "var(--color-text-secondary)", textTransform: "uppercase", letterSpacing: "0.5px" }}>Role</span>
              <select
                value={userRole}
                onChange={e => setUserRole(e.target.value)}
                style={{ padding: "6px 10px", borderRadius: "var(--border-radius-md)", border: "1px solid var(--color-border-secondary)", background: "var(--color-background-secondary)", fontSize: 12, fontWeight: 500, color: "var(--color-text-primary)" }}
              >
                {ROLES.map(r => <option key={r.value} value={r.value}>{r.label}</option>)}
              </select>
              <span style={{
                fontSize: 10, padding: "3px 8px", borderRadius: 999, fontWeight: 700, letterSpacing: "0.3px", whiteSpace: "nowrap",
                background: roleBadgeColor.bg,
                color: roleBadgeColor.fg,
              }}>
                {roleBadgeText}
              </span>
              {DEMO_MODE && (
                <span style={{ fontSize: 10, padding: "3px 8px", borderRadius: 999, fontWeight: 700, letterSpacing: "0.3px", whiteSpace: "nowrap", background: "#FEF3C7", color: "#92400E" }}>
                  DEMO
                </span>
              )}
            </div>
          </div>
        </div>

        {/* Status messages */}
        {(companyLoadMsg || companyLoadError) && (
          <div style={{ marginBottom: 10, fontSize: 12, color: companyLoadError ? "var(--color-red)" : "var(--color-green)", fontWeight: 500 }}>
            {companyLoadError || companyLoadMsg}
          </div>
        )}

        {/* ── Navigation Bar (Horizontal UX) ── */}
        <div
          style={{
            display: "grid",
            gap: 10,
            paddingBottom: 10,
            borderTop: "1px solid var(--color-border-tertiary)",
            paddingTop: 10,
          }}
        >
          {visibleModules.length > 0 ? (
            <>
              <div style={{ display: "flex", alignItems: "center", gap: 8, overflowX: "auto", scrollbarWidth: "none", msOverflowStyle: "none" }}>
                {visibleModules.map((module) => {
                  const isModuleActive = activeModule?.label === module.label;
                  return (
                    <button
                      key={module.label}
                      onClick={() => {
                        if (!module.tabs.includes(tab)) {
                          setTab(module.tabs[0]);
                        }
                      }}
                      style={{
                        padding: "7px 12px",
                        fontSize: 11,
                        fontWeight: isModuleActive ? 700 : 600,
                        borderRadius: 999,
                        border: isModuleActive ? "1px solid var(--color-blue)" : "1px solid var(--color-border-secondary)",
                        background: isModuleActive ? "var(--color-blue)" : "var(--color-background-primary)",
                        color: isModuleActive ? "#fff" : "var(--color-text-secondary)",
                        whiteSpace: "nowrap",
                        cursor: "pointer",
                        transition: "all var(--transition-fast)",
                      }}
                    >
                      {module.label}
                    </button>
                  );
                })}
              </div>

              <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
                <div style={{ fontSize: 10, fontWeight: 700, color: "var(--color-text-tertiary)", textTransform: "uppercase", letterSpacing: "0.5px", minWidth: 138 }}>
                  {activeModule?.label || "Section"}
                </div>
                <div style={{ display: "flex", gap: 6, overflowX: "auto", scrollbarWidth: "none", msOverflowStyle: "none", flex: 1 }}>
                  {(activeModule?.tabs || visibleTabs).map((t) => {
                    const isActive = tab === t;
                    return (
                      <button
                        key={t}
                        onClick={() => setTab(t)}
                        style={{
                          padding: "8px 12px",
                          fontSize: 12,
                          fontWeight: isActive ? 700 : 500,
                          border: isActive ? "1px solid var(--color-blue)" : "1px solid var(--color-border-secondary)",
                          borderRadius: "var(--border-radius-md)",
                          background: isActive ? "var(--color-blue)" : "var(--color-background-primary)",
                          color: isActive ? "#fff" : "var(--color-text-secondary)",
                          whiteSpace: "nowrap",
                          transition: "all var(--transition-fast)",
                          letterSpacing: isActive ? "0.1px" : 0,
                        }}
                        onMouseEnter={(e) => {
                          if (!isActive) {
                            e.currentTarget.style.background = "var(--color-background-secondary)";
                            e.currentTarget.style.color = "var(--color-text-primary)";
                          }
                        }}
                        onMouseLeave={(e) => {
                          if (!isActive) {
                            e.currentTarget.style.background = "var(--color-background-primary)";
                            e.currentTarget.style.color = "var(--color-text-secondary)";
                          }
                        }}
                      >
                        {t}
                      </button>
                    );
                  })}
                </div>
              </div>
            </>
          ) : (
            <div style={{ fontSize: 12, color: "var(--color-text-secondary)", paddingBottom: 6 }}>
              No navigation items available for this role.
            </div>
          )}
        </div>
      </div>

      {/* ── Page content with fade-in ─────────────────────────────────────── */}
      <div key={tab} className="fade-in">
      {tab === "Dashboard" && <DashboardTab refreshKey={refreshKey} period={period} userRole={userRole} activeCompany={activeCompany} />}
      {tab === "RevOps Control Center" && <RevOpsControlCenterTab refreshKey={refreshKey} activeCompany={activeCompany} period={period} userRole={userRole} />}
      {tab === "ARR Health" && <ArrHealthTab refreshKey={refreshKey} period={period} userRole={userRole} activeCompany={activeCompany} />}
      {tab === "ARR Waterfall" && <ARRWaterfallPage refreshKey={refreshKey} activeCompany={activeCompany} userRole={userRole} />}
      {tab === "Pipeline Health" && <PipelineHealthTab refreshKey={refreshKey} period={period} userRole={userRole} activeCompany={activeCompany} />}
      {tab === "Forecast"  && <ForecastTab refreshKey={refreshKey} activeCompany={activeCompany} period={period} userRole={userRole} />}
      {tab === "ML Insights" && <MLInsightsPage refreshKey={refreshKey} activeCompany={activeCompany} userRole={userRole} />}
      {tab === "Reps"      && <RepsTab refreshKey={refreshKey} period={period} userRole={userRole} activeCompany={activeCompany} />}
      {tab === "Rep Scorecard" && <RepScorecardPage refreshKey={refreshKey} activeCompany={activeCompany} userRole={userRole} />}
      {tab === "AI Agent"  && <AgentPage activeCompany={activeCompany} userRole={userRole} />}
      {tab === "Payouts"   && <PayoutsPage refreshKey={refreshKey} activeCompany={activeCompany} userRole={userRole} period={period} />}
      {tab === "Payout Approvals" && <PayoutAuditPage refreshKey={refreshKey} activeCompany={activeCompany} userRole={userRole} />}
      {tab === "Plans"     && <PlansPage refreshKey={refreshKey} userRole={userRole} activeCompany={activeCompany} />}
      {tab === "Territories" && <TerritoriesPage refreshKey={refreshKey} period={period} activeCompany={activeCompany} userRole={userRole} />}
      {tab === "Reports" && <ReportsTab activeCompany={activeCompany} userRole={userRole} />}
      {tab === "Data Quality" && <DataQualityTab refreshKey={refreshKey} activeCompany={activeCompany} userRole={userRole} />}
      {tab === "Model Monitoring" && <ModelMonitoringTab refreshKey={refreshKey} activeCompany={activeCompany} userRole={userRole} />}
      {tab === "Enterprise Grade" && <EnterpriseGradeTab refreshKey={refreshKey} activeCompany={activeCompany} userRole={userRole} />}
      {tab === "Org Hierarchy" && <OrgHierarchyPage refreshKey={refreshKey} activeCompany={activeCompany} userRole={userRole} />}
      {tab === "Ingestion" && <IngestionTab refreshKey={refreshKey} activeCompany={activeCompany} onCompanyLoaded={loadCompany} />}

      </div>
    </div>
  );
}
