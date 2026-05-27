/**
 * ARRWaterfallPage.jsx — Dedicated ARR waterfall analytics page.
 * Sprint 2.2
 */
import { useState } from "react";
import {
  BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid, Legend,
  LineChart, Line,
} from "recharts";
import { useFetch } from "../hooks/useFetch";
import { MetricCard, Skeleton, Card, SectionTitle, ErrorMessage } from "../components/shared";
import { fmt, withRefresh } from "../utils/format";

const WATERFALL_COLORS = {
  new_logo:    "#1D9E75",
  expansion:   "#378ADD",
  renewal:     "#85B7EB",
  contraction: "#EF9F27",
  churn:       "#D85A30",
};

function WaterfallChart({ data }) {
  if (!data || data.length === 0) return null;

  const chartData = data.map((d) => ({
    period: d.period,
    "New Logo": +(d.new_logo / 1000).toFixed(1),
    "Expansion": +(d.expansion / 1000).toFixed(1),
    "Renewal": +(d.renewal / 1000).toFixed(1),
    "Contraction": -(d.contraction / 1000).toFixed(1),
    "Churn": -(d.churn / 1000).toFixed(1),
    "Net ARR": +(d.net_new_arr / 1000).toFixed(1),
  }));

  return (
    <ResponsiveContainer width="100%" height={260}>
      <BarChart data={chartData} margin={{ left: 0 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border-tertiary)" />
        <XAxis dataKey="period" tick={{ fontSize: 10 }} tickFormatter={(v) => v.slice(5)} />
        <YAxis tick={{ fontSize: 11 }} tickFormatter={(v) => `$${v}K`} />
        <Tooltip formatter={(v) => [`$${Math.abs(v)}K`, undefined]} />
        <Legend wrapperStyle={{ fontSize: 11 }} />
        <Bar dataKey="New Logo" stackId="a" fill={WATERFALL_COLORS.new_logo} radius={0} />
        <Bar dataKey="Expansion" stackId="a" fill={WATERFALL_COLORS.expansion} radius={0} />
        <Bar dataKey="Renewal" stackId="a" fill={WATERFALL_COLORS.renewal} radius={0} />
        <Bar dataKey="Contraction" stackId="b" fill={WATERFALL_COLORS.contraction} radius={0} />
        <Bar dataKey="Churn" stackId="b" fill={WATERFALL_COLORS.churn} radius={0} />
      </BarChart>
    </ResponsiveContainer>
  );
}

function ArrTrendChart({ data }) {
  if (!data || data.length === 0) return null;
  const chartData = data.map((d) => ({
    period: d.period,
    "ARR End": +(d.arr_end / 1000).toFixed(1),
    "Net New ARR": +(d.net_new_arr / 1000).toFixed(1),
  }));
  return (
    <ResponsiveContainer width="100%" height={200}>
      <LineChart data={chartData}>
        <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border-tertiary)" />
        <XAxis dataKey="period" tick={{ fontSize: 10 }} tickFormatter={(v) => v.slice(5)} />
        <YAxis tick={{ fontSize: 11 }} tickFormatter={(v) => `$${v}K`} />
        <Tooltip formatter={(v) => `$${v}K`} />
        <Legend wrapperStyle={{ fontSize: 11 }} />
        <Line type="monotone" dataKey="ARR End" stroke="#378ADD" strokeWidth={2} dot={false} />
        <Line type="monotone" dataKey="Net New ARR" stroke="#1D9E75" strokeWidth={2} dot={false} />
      </LineChart>
    </ResponsiveContainer>
  );
}

export default function ARRWaterfallPage({ refreshKey }) {
  const [months, setMonths] = useState(12);

  // Use ML forecast arr-waterfall — returns real ARR derived from revenue data
  const { data, loading, error } = useFetch(
    withRefresh(`/ml/forecast/arr-waterfall`, refreshKey)
  );

  // Backend returns columnar arrays under data.waterfall
  const wRaw = data?.waterfall || {};
  const allPeriods = wRaw.periods || [];
  const sliceIdx = Math.max(0, allPeriods.length - months);

  const seriesData = allPeriods.slice(sliceIdx).map((p, i) => {
    const idx = sliceIdx + i;
    return {
      period: p,
      new_logo:    wRaw.new_logo?.[idx]    ?? 0,
      expansion:   wRaw.expansion?.[idx]   ?? 0,
      renewal:     wRaw.renewal?.[idx]     ?? 0,
      contraction: wRaw.contraction?.[idx] ?? 0,
      churn:       wRaw.churn?.[idx]       ?? 0,
      net_new_arr: wRaw.net_new_arr?.[idx] ?? 0,
      arr_start:   wRaw.arr_start?.[idx]   ?? 0,
      arr_end:     wRaw.arr_end?.[idx]     ?? 0,
    };
  });

  const latest = seriesData[seriesData.length - 1];

  // Aggregate across all periods for KPIs
  const totals = seriesData.reduce(
    (acc, d) => ({
      new_logo: acc.new_logo + d.new_logo,
      expansion: acc.expansion + d.expansion,
      churn: acc.churn + d.churn,
      contraction: acc.contraction + d.contraction,
      renewal: acc.renewal + d.renewal,
      net_new_arr: acc.net_new_arr + d.net_new_arr,
    }),
    { new_logo: 0, expansion: 0, churn: 0, contraction: 0, renewal: 0, net_new_arr: 0 }
  );

  // Derived insights
  const gross = totals.new_logo + totals.expansion + totals.renewal;
  const loss = totals.churn + totals.contraction;
  const churnPct = gross > 0 ? (totals.churn / gross) * 100 : 0;
  const expansionPct = gross > 0 ? (totals.expansion / gross) * 100 : 0;
  const newLogoPct = gross > 0 ? (totals.new_logo / gross) * 100 : 0;
  const dominantInflow = totals.new_logo >= totals.expansion ? "new logo" : "expansion";

  // Month-over-month acceleration: compare last 3 vs prior 3
  const recentSlice = seriesData.slice(-3);
  const priorSlice = seriesData.slice(-6, -3);
  const recentNetAvg = recentSlice.length > 0 ? recentSlice.reduce((s, d) => s + d.net_new_arr, 0) / recentSlice.length : 0;
  const priorNetAvg = priorSlice.length > 0 ? priorSlice.reduce((s, d) => s + d.net_new_arr, 0) / priorSlice.length : 0;
  const momAccel = priorNetAvg !== 0 ? ((recentNetAvg - priorNetAvg) / Math.abs(priorNetAvg)) * 100 : null;

  // Best and worst net ARR periods
  const bestPeriod = seriesData.length > 0 ? seriesData.reduce((a, b) => b.net_new_arr > a.net_new_arr ? b : a) : null;
  const worstPeriod = seriesData.length > 0 ? seriesData.reduce((a, b) => b.net_new_arr < a.net_new_arr ? b : a) : null;

  return (
    <div>
      <div style={{ display: "flex", gap: 10, alignItems: "center", marginBottom: "1.5rem" }}>
        <span style={{ fontSize: 12, color: "var(--color-text-secondary)" }}>Trailing months:</span>
        {[3, 6, 12, 24].map((m) => (
          <button
            key={m}
            onClick={() => setMonths(m)}
            style={{
              padding: "4px 12px",
              borderRadius: 6,
              border: "1px solid var(--color-border-tertiary)",
              background: months === m ? "var(--color-accent-primary)" : "transparent",
              color: months === m ? "#fff" : "var(--color-text-primary)",
              cursor: "pointer",
              fontSize: 12,
            }}
          >
            {m}M
          </button>
        ))}
      </div>

      {loading && <Skeleton h={300} />}
      {error && <ErrorMessage message={error} />}
      {!loading && !error && seriesData.length === 0 && (
        <div style={{ fontSize: 13, color: "var(--color-text-secondary)" }}>
          No ARR waterfall data available. Upload arr_waterfall.csv or ensure bookings/churn data is present.
        </div>
      )}

      {!loading && !error && seriesData.length > 0 && (
        <>
          {/* KPI bar */}
          <div style={{ display: "grid", gridTemplateColumns: "repeat(5,1fr)", gap: 10, marginBottom: "1rem" }}>
            <MetricCard label="New Logo ARR" value={fmt(totals.new_logo)} sub={`${months}M total`} color="#1D9E75" />
            <MetricCard label="Expansion ARR" value={fmt(totals.expansion)} sub={`${months}M total`} color="#378ADD" />
            <MetricCard label="Renewal ARR" value={fmt(totals.renewal)} sub={`${months}M total`} color="#85B7EB" />
            <MetricCard label="Churn ARR" value={fmt(totals.churn)} sub={`${months}M total`} color="#D85A30" />
            <MetricCard
              label="Net New ARR"
              value={fmt(Math.abs(totals.net_new_arr))}
              sub={totals.net_new_arr >= 0 ? "▲ growth" : "▼ decline"}
              color={totals.net_new_arr >= 0 ? "#1D9E75" : "#D85A30"}
            />
          </div>

          {/* ARR Motion Executive Summary */}
          {gross > 0 && (() => {
            const netPositive = totals.net_new_arr >= 0;
            const bg = netPositive ? "rgba(29,158,117,0.06)" : "rgba(216,90,48,0.06)";
            const border = netPositive ? "#1D9E7530" : "#D85A3030";
            const icon = netPositive ? "✅" : "🔴";
            const headline = netPositive
              ? `ARR is expanding — ${dominantInflow} is the primary growth driver over the last ${months} months.`
              : `ARR is contracting — losses from churn and contraction are outpacing new inflows.`;
            return (
              <div style={{ background: bg, border: `1px solid ${border}`, borderRadius: 8, padding: "12px 16px", marginBottom: "1.25rem", fontSize: 12 }}>
                <div style={{ fontWeight: 600, marginBottom: 8, fontSize: 12.5 }}>{icon} {headline}</div>
                <div style={{ display: "flex", flexWrap: "wrap", gap: "4px 20px", color: "var(--color-text-secondary)", lineHeight: 1.6 }}>
                  <span>Gross inflows: <strong style={{ color: "#1D9E75" }}>{fmt(gross)}</strong></span>
                  <span>Gross outflows: <strong style={{ color: "#D85A30" }}>{fmt(loss)}</strong></span>
                  <span>Net: <strong style={{ color: totals.net_new_arr >= 0 ? "#1D9E75" : "#D85A30" }}>{totals.net_new_arr >= 0 ? "+" : ""}{fmt(totals.net_new_arr)}</strong></span>
                  {newLogoPct > 0 && <span>New logo: <strong>{newLogoPct.toFixed(0)}%</strong> of gross inflow</span>}
                  {expansionPct > 0 && <span>Expansion: <strong>{expansionPct.toFixed(0)}%</strong> of gross inflow</span>}
                  {churnPct > 15 && <span style={{ color: "#D85A30" }}>⚠ Churn consuming {churnPct.toFixed(1)}% of gross inflows — above healthy 10–15% band.</span>}
                  {churnPct <= 10 && <span style={{ color: "#1D9E75" }}>Churn at {churnPct.toFixed(1)}% of gross inflows is within healthy range.</span>}
                  {momAccel !== null && (
                    <span>
                      Recent momentum ({recentSlice.length}M avg): <strong style={{ color: momAccel >= 0 ? "#1D9E75" : "#D85A30" }}>
                        {momAccel >= 0 ? "+" : ""}{momAccel.toFixed(0)}%
                      </strong> vs prior period — {momAccel >= 10 ? "accelerating" : momAccel >= -5 ? "stable" : "decelerating"}.
                    </span>
                  )}
                </div>
              </div>
            );
          })()}

          {/* Latest period detail */}
          {latest && (
            <div style={{ display: "grid", gridTemplateColumns: "repeat(2,1fr)", gap: 10, marginBottom: "1.5rem" }}>
              <MetricCard label={`ARR Start (${latest.period})`} value={fmt(latest.arr_start)} />
              <MetricCard label={`ARR End (${latest.period})`} value={fmt(latest.arr_end)} />
            </div>
          )}

          {/* Waterfall bar chart */}
          <Card style={{ marginBottom: "1.5rem" }}>
            <SectionTitle>Monthly ARR waterfall components</SectionTitle>
            <WaterfallChart data={seriesData} />
            {bestPeriod && (
              <div style={{ marginTop: 12, padding: "10px 14px", borderRadius: 8, background: "var(--color-background-secondary)", border: "0.5px solid var(--color-border-tertiary)", fontSize: 11, color: "var(--color-text-secondary)", display: "flex", flexWrap: "wrap", gap: "4px 18px", lineHeight: 1.6 }}>
                <span>Best period: <strong style={{ color: "#1D9E75" }}>{bestPeriod.period}</strong> with net ARR of <strong>{fmt(bestPeriod.net_new_arr)}</strong></span>
                {worstPeriod && worstPeriod.period !== bestPeriod.period && (
                  <span>Trough: <strong style={{ color: worstPeriod.net_new_arr < 0 ? "#D85A30" : "var(--color-text-primary)" }}>{worstPeriod.period}</strong> at <strong>{fmt(worstPeriod.net_new_arr)}</strong></span>
                )}
                {bestPeriod && worstPeriod && bestPeriod.period !== worstPeriod.period && (
                  <span>Spread between peak and trough: <strong>{fmt(bestPeriod.net_new_arr - worstPeriod.net_new_arr)}</strong> — {(bestPeriod.net_new_arr - worstPeriod.net_new_arr) > gross * 0.2 ? "high seasonality or lumpy deal flow" : "relatively consistent ARR velocity"}.</span>
                )}
              </div>
            )}
          </Card>

          {/* ARR trend */}
          <Card style={{ marginBottom: "1.5rem" }}>
            <SectionTitle>ARR trend</SectionTitle>
            <ArrTrendChart data={seriesData} />
            {seriesData.length >= 2 && (() => {
              const first = seriesData[0];
              const last = seriesData[seriesData.length - 1];
              const arrChange = last.arr_end - first.arr_start;
              const arrChangePct = first.arr_start > 0 ? (arrChange / first.arr_start) * 100 : null;
              const direction = arrChange >= 0 ? "grown" : "declined";
              const color = arrChange >= 0 ? "#1D9E75" : "#D85A30";
              return (
                <div style={{ marginTop: 12, padding: "10px 14px", borderRadius: 8, background: "var(--color-background-secondary)", border: "0.5px solid var(--color-border-tertiary)", fontSize: 11, color: "var(--color-text-secondary)", lineHeight: 1.6 }}>
                  <span>ARR has <strong style={{ color }}>{direction}</strong> from <strong>{fmt(first.arr_start)}</strong> to <strong>{fmt(last.arr_end)}</strong> over {months} months</span>
                  {arrChangePct !== null && (
                    <span style={{ marginLeft: 16 }}>({arrChangePct >= 0 ? "+" : ""}{arrChangePct.toFixed(1)}% change)</span>
                  )}
                  {arrChangePct !== null && arrChangePct >= 20 && <span style={{ marginLeft: 16, color: "#1D9E75" }}>Above 20% growth — on track for high-growth SaaS benchmarks.</span>}
                  {arrChangePct !== null && arrChangePct < 0 && <span style={{ marginLeft: 16, color: "#D85A30" }}>ARR base is shrinking — churn and contraction require immediate attention.</span>}
                </div>
              );
            })()}
          </Card>

          {/* Detail table */}
          <Card>
            <SectionTitle>Monthly breakdown</SectionTitle>

            {/* Contextual summary */}
            {(() => {
              if (seriesData.length < 2) return null;

              // Growth composition over period
              const totalGross = totals.new_logo + totals.expansion + totals.renewal;
              const nlShare  = totalGross > 0 ? (totals.new_logo  / totalGross) * 100 : 0;
              const expShare = totalGross > 0 ? (totals.expansion / totalGross) * 100 : 0;
              const renShare = totalGross > 0 ? (totals.renewal   / totalGross) * 100 : 0;
              const churnRate = totalGross > 0 ? (totals.churn / totalGross) * 100 : 0;
              const contractionRate = totalGross > 0 ? (totals.contraction / totalGross) * 100 : 0;

              // NRR proxy: (renewal + expansion - contraction - churn) / renewal (period avg)
              const nrrProxy = totals.renewal > 0
                ? Math.round(((totals.renewal + totals.expansion - totals.contraction - totals.churn) / totals.renewal) * 100)
                : null;

              // Consecutive positive net ARR months
              const reversed = [...seriesData].reverse();
              let consecutivePositive = 0;
              for (const d of reversed) {
                if (d.net_new_arr > 0) consecutivePositive++;
                else break;
              }

              // Growth motion label
              const dominantMotion =
                expShare > nlShare && expShare > renShare ? "expansion-led" :
                nlShare  > expShare && nlShare  > renShare ? "new-logo-led" :
                "renewal-led";

              // Month with highest churn
              const worstChurnPeriod = seriesData.length > 0
                ? seriesData.reduce((a, b) => b.churn > a.churn ? b : a)
                : null;

              // Avg monthly net ARR
              const avgMonthlyNet = seriesData.reduce((s, d) => s + d.net_new_arr, 0) / seriesData.length;
              const positiveMonths = seriesData.filter(d => d.net_new_arr > 0).length;
              const negativeMonths = seriesData.filter(d => d.net_new_arr < 0).length;

              // Trend direction: last 3 months net avg vs first 3 months net avg
              const earlyAvg = seriesData.slice(0, 3).reduce((s, d) => s + d.net_new_arr, 0) / 3;
              const lateAvg  = seriesData.slice(-3).reduce((s, d) => s + d.net_new_arr, 0) / 3;
              const overallTrend = lateAvg > earlyAvg * 1.1 ? "improving" : lateAvg < earlyAvg * 0.9 ? "deteriorating" : "stable";

              const insights = [];

              // 1. Growth motion
              if (dominantMotion === "expansion-led") {
                insights.push({ icon: "💡", color: "#378ADD", text: `<strong>Expansion-led growth (${expShare.toFixed(0)}% of inflows)</strong> — strong signal of product stickiness and upsell motion. Net Revenue Retention is the key metric to protect here. Ensure CSMs have upsell quotas and expansion playbooks are active.` });
              } else if (dominantMotion === "new-logo-led") {
                insights.push({ icon: "🎯", color: "#1D9E75", text: `<strong>New-logo-led growth (${nlShare.toFixed(0)}% of inflows)</strong> — acquisition engine is driving ARR. Watch churn closely: high new-logo dependency can mask retention weakness. Pair with expansion programs to improve LTV.` });
              } else {
                insights.push({ icon: "🔁", color: "#85B7EB", text: `<strong>Renewal-led growth (${renShare.toFixed(0)}% of inflows)</strong> — base is sticky but upside is limited. Expansion and new logo motions need acceleration to compound ARR meaningfully.` });
              }

              // 2. Churn health
              if (churnRate > 15) {
                insights.push({ icon: "⚠️", color: "#D85A30", text: `<strong>Churn at ${churnRate.toFixed(1)}% of gross inflows is above the healthy 10–15% SaaS benchmark.</strong> Highest churn month was ${worstChurnPeriod?.period} (${fmt(worstChurnPeriod?.churn)}). Retention plays — health scores, QBRs, and at-risk triggers — should be prioritised by CSMs this quarter.` });
              } else if (churnRate <= 5) {
                insights.push({ icon: "✅", color: "#1D9E75", text: `<strong>Churn is low at ${churnRate.toFixed(1)}% of inflows</strong> — strong retention quality. This compounds favourably with expansion: every dollar retained earns expansion revenue on top.` });
              } else {
                insights.push({ icon: "📊", color: "#EF9F27", text: `Churn at ${churnRate.toFixed(1)}% of inflows is within acceptable range. Monitor month-to-month: a drift above 15% would signal retention risk.` });
              }

              // 3. Contraction signal
              if (contractionRate > 5) {
                insights.push({ icon: "📉", color: "#EF9F27", text: `Contraction is elevated at ${contractionRate.toFixed(1)}% of inflows — customers are downsizing contracts. Review downsell patterns: are contractions driven by specific segments, product tiers, or renewal cycles? Sales and CS alignment is needed to protect seat counts at renewal.` });
              }

              // 4. NRR proxy
              if (nrrProxy !== null) {
                const nrrColor = nrrProxy >= 110 ? "#1D9E75" : nrrProxy >= 100 ? "#378ADD" : "#D85A30";
                insights.push({ icon: "📈", color: nrrColor, text: `<strong>Net Revenue Retention proxy: ~${nrrProxy}%</strong>${nrrProxy >= 110 ? " — world-class NRR. Expansion is more than offsetting churn, enabling compounding growth without new logos." : nrrProxy >= 100 ? " — NRR above 100% means the existing base is self-sustaining. New logos are pure upside." : " — NRR below 100% means the base is eroding. New logos must outrun churn to show net growth."}` });
              }

              // 5. Overall trend
              insights.push({ icon: overallTrend === "improving" ? "📈" : overallTrend === "deteriorating" ? "📉" : "➡️", color: overallTrend === "improving" ? "#1D9E75" : overallTrend === "deteriorating" ? "#D85A30" : "#EF9F27", text: `<strong>Trend: ${overallTrend}</strong> — avg monthly net ARR was ${fmt(Math.round(earlyAvg))} early in the period vs ${fmt(Math.round(lateAvg))} recently. ${positiveMonths}/${seriesData.length} months were net-positive.${consecutivePositive >= 3 ? ` Last ${consecutivePositive} months have been consecutively positive — strong momentum signal for the sales team.` : negativeMonths > seriesData.length / 2 ? " More than half the months were net-negative — a pipeline and retention intervention may be needed." : ""}` });

              // 6. Sales action point
              const salesAction =
                dominantMotion === "expansion-led" && churnRate > 10
                  ? "Sales focus: protect upsell accounts with proactive renewal outreach. Churn is eroding expansion gains."
                  : dominantMotion === "new-logo-led" && expShare < 20
                  ? "Sales focus: activate expansion plays on the new-logo cohort. Low expansion share means closed deals aren't being developed post-sale."
                  : overallTrend === "deteriorating"
                  ? "Sales focus: pipeline urgency — deteriorating net ARR trend requires both new logo acceleration and churn containment working in parallel."
                  : `Sales focus: maintain current motion. ${dominantMotion === "expansion-led" ? "Protect and expand the installed base." : "Keep new logo pipeline full while building expansion playbooks for closed accounts."}`;
              insights.push({ icon: "🏆", color: "#378ADD", text: salesAction });

              return (
                <div style={{ marginBottom: 14, display: "flex", flexDirection: "column", gap: 8 }}>
                  {/* Composition bar */}
                  <div style={{ marginBottom: 4 }}>
                    <div style={{ fontSize: 11, color: "var(--color-text-secondary)", marginBottom: 5 }}>Gross inflow composition — {months}M</div>
                    <div style={{ display: "flex", height: 10, borderRadius: 5, overflow: "hidden", gap: 1 }}>
                      {nlShare  > 0 && <div style={{ width: `${nlShare}%`,  background: "#1D9E75" }} title={`New Logo ${nlShare.toFixed(0)}%`} />}
                      {expShare > 0 && <div style={{ width: `${expShare}%`, background: "#378ADD" }} title={`Expansion ${expShare.toFixed(0)}%`} />}
                      {renShare > 0 && <div style={{ width: `${renShare}%`, background: "#85B7EB" }} title={`Renewal ${renShare.toFixed(0)}%`} />}
                    </div>
                    <div style={{ display: "flex", gap: 14, marginTop: 4, fontSize: 10, color: "var(--color-text-secondary)" }}>
                      <span><span style={{ color: "#1D9E75", fontWeight: 600 }}>■</span> New Logo {nlShare.toFixed(0)}%</span>
                      <span><span style={{ color: "#378ADD", fontWeight: 600 }}>■</span> Expansion {expShare.toFixed(0)}%</span>
                      <span><span style={{ color: "#85B7EB", fontWeight: 600 }}>■</span> Renewal {renShare.toFixed(0)}%</span>
                      <span style={{ marginLeft: "auto", color: "#D85A30" }}>Churn {churnRate.toFixed(1)}%</span>
                      {contractionRate > 0 && <span style={{ color: "#EF9F27" }}>Contraction {contractionRate.toFixed(1)}%</span>}
                    </div>
                  </div>

                  {/* Insight cards */}
                  <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                    {insights.map((ins, i) => (
                      <div key={i} style={{
                        padding: "8px 12px",
                        borderRadius: 6,
                        background: "var(--color-background-secondary)",
                        borderLeft: `3px solid ${ins.color}`,
                        fontSize: 11.5,
                        color: "var(--color-text-primary)",
                        lineHeight: 1.55,
                      }}>
                        <span style={{ marginRight: 6 }}>{ins.icon}</span>
                        <span dangerouslySetInnerHTML={{ __html: ins.text }} />
                      </div>
                    ))}
                  </div>
                </div>
              );
            })()}

            <div style={{ overflowX: "auto" }}>
              <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
                <thead>
                  <tr style={{ color: "var(--color-text-secondary)", fontSize: 11 }}>
                    {["Period", "New Logo", "Expansion", "Renewal", "Contraction", "Churn", "Net New ARR", "ARR End", "Signal"].map((h) => (
                      <th key={h} style={{ textAlign: h === "Period" || h === "Signal" ? "left" : "right", padding: "4px 8px", fontWeight: 400 }}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {(() => {
                    const rows = [...seriesData].reverse();
                    return rows.map((d, i) => {
                      // Prior period in this reversed array is i+1
                      const prior = rows[i + 1];
                      const netDelta = prior ? d.net_new_arr - prior.net_new_arr : null;
                      const totalLoss = d.churn + d.contraction;
                      const totalGain = d.new_logo + d.expansion + d.renewal;
                      const churnDominant = d.churn > d.new_logo && d.churn > d.expansion;

                      let signal, signalColor;
                      if (d.net_new_arr > 0 && netDelta !== null && netDelta > 0) {
                        signal = "↑ Accelerating"; signalColor = "#1D9E75";
                      } else if (d.net_new_arr > 0 && (netDelta === null || Math.abs(netDelta) < totalGain * 0.05)) {
                        signal = "→ Steady";       signalColor = "#378ADD";
                      } else if (d.net_new_arr > 0) {
                        signal = "↗ Positive";     signalColor = "#59C099";
                      } else if (d.net_new_arr < 0 && churnDominant) {
                        signal = "↓ Churn risk";   signalColor = "#D85A30";
                      } else if (d.net_new_arr < 0) {
                        signal = "↓ Contracting";  signalColor = "#EF9F27";
                      } else {
                        signal = "= Flat";          signalColor = "#9CA3AF";
                      }

                      return (
                        <tr key={d.period} style={{ borderTop: "0.5px solid var(--color-border-tertiary)" }}>
                          <td style={{ padding: "5px 8px", fontWeight: 500 }}>{d.period}</td>
                          <td style={{ textAlign: "right", padding: "5px 8px", color: "#1D9E75" }}>{fmt(d.new_logo)}</td>
                          <td style={{ textAlign: "right", padding: "5px 8px", color: "#378ADD" }}>{fmt(d.expansion)}</td>
                          <td style={{ textAlign: "right", padding: "5px 8px", color: "#85B7EB" }}>{fmt(d.renewal)}</td>
                          <td style={{ textAlign: "right", padding: "5px 8px", color: "#EF9F27" }}>({fmt(d.contraction)})</td>
                          <td style={{ textAlign: "right", padding: "5px 8px", color: "#D85A30" }}>({fmt(d.churn)})</td>
                          <td style={{ textAlign: "right", padding: "5px 8px", fontWeight: 600, color: d.net_new_arr >= 0 ? "#1D9E75" : "#D85A30" }}>
                            {d.net_new_arr >= 0 ? "+" : ""}{fmt(d.net_new_arr)}
                          </td>
                          <td style={{ textAlign: "right", padding: "5px 8px" }}>{fmt(d.arr_end)}</td>
                          <td style={{ padding: "5px 8px" }}>
                            <span style={{
                              padding: "2px 7px", borderRadius: 8, fontSize: 10, fontWeight: 500,
                              background: `${signalColor}18`, color: signalColor, whiteSpace: "nowrap",
                            }}>{signal}</span>
                          </td>
                        </tr>
                      );
                    });
                  })()}
                </tbody>
              </table>
            </div>
          </Card>
        </>
      )}
    </div>
  );
}
