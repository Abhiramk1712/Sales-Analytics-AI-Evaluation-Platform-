/**
 * RepScorecardPage.jsx — Detailed rep scorecard with activity, deals, and quota trends.
 * Sprint 2.5 / 3.5
 */
import { useEffect, useMemo, useState } from "react";
import {
  BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid, Cell, Legend,
} from "recharts";
import { useFetch } from "../hooks/useFetch";
import { MetricCard, Skeleton, Card, SectionTitle, ErrorMessage, PaginationControls } from "../components/shared";
import { fmt, pct, withRefresh, withPeriod } from "../utils/format";
import { useUrlState } from "../hooks/useUrlState";

const STAGE_COLORS = {
  Prospecting: "#B5D4F4", Qualification: "#85B7EB", Proposal: "#378ADD",
  Negotiation: "#185FA5", "Closed Won": "#1D9E75", "Closed Lost": "#D85A30",
};

function ActivityTypeTag({ type }) {
  const COLORS = { call: "#378ADD", email: "#85B7EB", meeting: "#1D9E75", demo: "#EF9F27" };
  return (
    <span style={{
      display: "inline-block", padding: "1px 7px", borderRadius: 10, fontSize: 10,
      background: COLORS[type?.toLowerCase()] || "#ccc", color: "#fff",
    }}>
      {type || "Other"}
    </span>
  );
}

export default function RepScorecardPage({ refreshKey, activeCompany, userRole, period }) {
  const role = userRole || "executive";
  const company = activeCompany || "";
  const [selectedRepId, setSelectedRepId] = useState(null);
  // A scorecard is the most link-worthy view in the app; the selected rep and
  // pane both belong in the URL.
  const [subview, setSubview] = useUrlState({ scorecard: "overview" });
  const activeTab = subview.scorecard;
  const setActiveTab = (next) => setSubview({ scorecard: next });
  const [dealsPage, setDealsPage] = useState(1);
  const [dealsPageSize, setDealsPageSize] = useState(10);
  const [activitiesPage, setActivitiesPage] = useState(1);
  const [activitiesPageSize, setActivitiesPageSize] = useState(10);

  const { data: repsData, loading: repsLoading } = useFetch(withPeriod(withRefresh("/analytics/reps/performance", refreshKey), period), { role, company });
  const { data: profileData, loading: profileLoading } = useFetch(
    selectedRepId ? withPeriod(`/analytics/reps/${selectedRepId}/profile`, period) : null, { role, company }
  );
  const { data: dealsData, loading: dealsLoading } = useFetch(
    selectedRepId && activeTab === "deals" ? `/analytics/reps/${selectedRepId}/deals?limit=50` : null, { role, company }
  );
  const { data: activitiesData, loading: activitiesLoading } = useFetch(
    selectedRepId && activeTab === "activities" ? `/analytics/reps/${selectedRepId}/activities?limit=50` : null, { role, company }
  );
  const { data: stmtData, loading: stmtLoading } = useFetch(
    selectedRepId && activeTab === "quota" ? `/payout/statements/${selectedRepId}?periods=12` : null, { role, company }
  );
  const { data: attainData, loading: attainLoading } = useFetch(
    activeTab === "forecast" ? withRefresh("/ml/forecast/rep-attainment", refreshKey) : null
  );

  const sortedReps = useMemo(
    () => [...(repsData || [])].sort((a, b) => Number(b.attainment_pct || 0) - Number(a.attainment_pct || 0)),
    [repsData]
  );

  // Reset selection when company changes so stale UUIDs from the previous
  // dataset don't get used with the new company's data.
  useEffect(() => {
    setSelectedRepId(null);
  }, [refreshKey]);

  useEffect(() => {
    if (selectedRepId || !sortedReps.length) return;
    setSelectedRepId(sortedReps[0].rep_id);
  }, [selectedRepId, sortedReps]);

  const selectedRep = sortedReps.find((r) => r.rep_id === selectedRepId);
  const allDeals = dealsData?.deals || [];
  const dealsPages = Math.max(1, Math.ceil(allDeals.length / Math.max(1, dealsPageSize)));
  const safeDealsPage = Math.min(dealsPage, dealsPages);
  const pagedDeals = useMemo(() => {
    const start = (safeDealsPage - 1) * dealsPageSize;
    return allDeals.slice(start, start + dealsPageSize);
  }, [allDeals, safeDealsPage, dealsPageSize]);

  const allActivities = activitiesData?.activities || [];
  const activitiesPages = Math.max(1, Math.ceil(allActivities.length / Math.max(1, activitiesPageSize)));
  const safeActivitiesPage = Math.min(activitiesPage, activitiesPages);
  const pagedActivities = useMemo(() => {
    const start = (safeActivitiesPage - 1) * activitiesPageSize;
    return allActivities.slice(start, start + activitiesPageSize);
  }, [allActivities, safeActivitiesPage, activitiesPageSize]);

  return (
    <div style={{ display: "grid", gridTemplateColumns: "220px 1fr", gap: 16 }}>
      {/* Rep list sidebar */}
      <div>
        <div style={{ fontSize: 11, textTransform: "uppercase", letterSpacing: "0.4px", color: "var(--color-text-secondary)", marginBottom: 8 }}>
          Reps ({repsData?.length ?? 0})
        </div>
        {repsLoading ? <Skeleton h={300} /> : (
          <div>
            {sortedReps.map((r) => (
              <div
                key={r.rep_id}
                onClick={() => {
                  setSelectedRepId(r.rep_id);
                  setActiveTab("overview");
                  setDealsPage(1);
                  setActivitiesPage(1);
                }}
                style={{
                  padding: "8px 10px",
                  borderRadius: 6,
                  cursor: "pointer",
                  marginBottom: 4,
                  background: selectedRepId === r.rep_id ? "#378ADD" : "var(--color-background-secondary)",
                  border: selectedRepId === r.rep_id ? "1px solid #2563EB" : "1px solid transparent",
                  color: selectedRepId === r.rep_id ? "#ffffff" : "var(--color-text-primary)",
                  fontSize: 12,
                }}
              >
                <div style={{ fontWeight: 500 }}>{r.name}</div>
                <div style={{ fontSize: 10, opacity: 0.7 }}>
                  {pct(r.attainment_pct)} attainment
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Detail panel */}
      <div>
        {!selectedRepId && (
          <div style={{ color: "var(--color-text-secondary)", fontSize: 13, paddingTop: 40 }}>
            Select a rep to view their scorecard.
          </div>
        )}

        {selectedRepId && (
          <>
            {/* Header */}
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: "1rem" }}>
              <div>
                <div style={{ fontSize: 18, fontWeight: 600 }}>{selectedRep?.name}</div>
                <div style={{ fontSize: 12, color: "var(--color-text-secondary)" }}>
                  {selectedRep?.email} · {selectedRep?.region || "—"}
                </div>
              </div>
            </div>

            {/* Tab navigation */}
            <div style={{ display: "flex", gap: 6, marginBottom: "1rem" }}>
              {["overview", "deals", "activities", "quota", "forecast"].map((t) => (
                <button
                  key={t}
                  onClick={() => setActiveTab(t)}
                  style={{
                    padding: "5px 14px",
                    borderRadius: 6,
                    border: activeTab === t ? "1px solid #2563EB" : "1px solid var(--color-border-secondary)",
                    background: activeTab === t ? "#378ADD" : "transparent",
                    color: activeTab === t ? "#ffffff" : "var(--color-text-primary)",
                    cursor: "pointer",
                    fontSize: 12,
                    fontWeight: activeTab === t ? 600 : 400,
                    textTransform: "capitalize",
                  }}
                >
                  {t}
                </button>
              ))}
            </div>

            {/* Overview */}
            {activeTab === "overview" && (
              profileLoading ? <Skeleton h={250} /> :
              profileData ? (
                <div>
                  {(() => {
                    const perf = profileData.performance || {};
                    const perfRevenue = Number(perf.revenue ?? selectedRep?.revenue ?? 0);
                    const perfAttainment = Number(perf.attainment_pct ?? selectedRep?.attainment_pct ?? 0);
                    const perfWinRate = Number(perf.win_rate ?? selectedRep?.win_rate ?? 0);
                    const dealsWon = Number(perf.deals_won ?? selectedRep?.deals_won ?? 0);
                    const dealsLost = Number(perf.deals_lost ?? selectedRep?.deals_lost ?? 0);
                    const avgDealSize = Number(
                      perf.average_deal_size
                      ?? selectedRep?.average_deal_size
                      ?? (dealsWon > 0 ? perfRevenue / dealsWon : 0)
                    );

                    return (
                      <>
                  <div style={{ display: "grid", gridTemplateColumns: "repeat(3,1fr)", gap: 10, marginBottom: "1rem" }}>
                    <MetricCard label="Revenue" value={fmt(perfRevenue)} />
                    <MetricCard label="Attainment" value={pct(perfAttainment)} color={perfAttainment >= 100 ? "#1D9E75" : "#D85A30"} />
                    <MetricCard label="Win Rate" value={pct(perfWinRate)} sub={`${dealsWon} won`} />
                  </div>
                  <div style={{ display: "grid", gridTemplateColumns: "repeat(3,1fr)", gap: 10 }}>
                    <MetricCard label="Deals Won" value={dealsWon} />
                    <MetricCard label="Deals Lost" value={dealsLost} />
                    <MetricCard label="Avg Deal Size" value={avgDealSize > 0 ? fmt(avgDealSize) : "—"} sub={avgDealSize > 0 ? "Closed-won average" : "No closed-won deals yet"} />
                  </div>
                      </>
                    );
                  })()}
                </div>
              ) : (
                <div style={{ color: "var(--color-text-secondary)", fontSize: 13 }}>Profile data unavailable.</div>
              )
            )}

            {/* Deals */}
            {activeTab === "deals" && (
              dealsLoading ? <Skeleton h={300} /> :
              dealsData ? (
                <Card>
                  <SectionTitle>Deals ({dealsData.count})</SectionTitle>
                  <div style={{ fontSize: 11, color: "var(--color-text-secondary)", marginBottom: 8 }}>
                    Prioritize late-stage deals with high probability and aging close dates for highest near-term impact.
                  </div>
                  <div style={{ overflowX: "auto" }}>
                    <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
                      <thead>
                        <tr style={{ color: "var(--color-text-secondary)", fontSize: 11 }}>
                          {["Deal", "Account", "Stage", "Amount", "Prob %", "Close Date"].map((h) => (
                            <th key={h} style={{ textAlign: h === "Deal" || h === "Account" || h === "Stage" ? "left" : "right", padding: "4px 8px", fontWeight: 400 }}>{h}</th>
                          ))}
                        </tr>
                      </thead>
                      <tbody>
                        {pagedDeals.map((d) => (
                          <tr key={d.deal_id} style={{ borderTop: "0.5px solid var(--color-border-tertiary)" }}>
                            <td style={{ padding: "5px 8px", maxWidth: 160, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{d.name}</td>
                            <td style={{ padding: "5px 8px" }}>{d.account || "—"}</td>
                            <td style={{ padding: "5px 8px" }}>
                              <span style={{ padding: "2px 7px", borderRadius: 10, background: STAGE_COLORS[d.stage] || "#ccc", color: d.stage.includes("Closed") ? "#fff" : "#333", fontSize: 10 }}>
                                {d.stage}
                              </span>
                            </td>
                            <td style={{ textAlign: "right", padding: "5px 8px" }}>{fmt(d.amount)}</td>
                            <td style={{ textAlign: "right", padding: "5px 8px" }}>{d.close_probability ?? "—"}</td>
                            <td style={{ textAlign: "right", padding: "5px 8px", color: "var(--color-text-secondary)" }}>
                              {d.expected_close_date || d.actual_close_date || "—"}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                  <PaginationControls
                    page={safeDealsPage}
                    pageSize={dealsPageSize}
                    totalItems={allDeals.length}
                    onPageChange={setDealsPage}
                    onPageSizeChange={(next) => {
                      setDealsPageSize(next);
                      setDealsPage(1);
                    }}
                  />
                </Card>
              ) : <ErrorMessage message="Could not load deals" />
            )}

            {/* Activities */}
            {activeTab === "activities" && (
              activitiesLoading ? <Skeleton h={300} /> :
              activitiesData ? (
                <Card>
                  <SectionTitle>Recent activities ({activitiesData.count})</SectionTitle>
                  <div style={{ fontSize: 11, color: "var(--color-text-secondary)", marginBottom: 8 }}>
                    Activity consistency matters: monitor sequence quality (calls -&gt; meetings -&gt; demos) rather than raw volume alone.
                  </div>
                  <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
                    <thead>
                      <tr style={{ color: "var(--color-text-secondary)", fontSize: 11 }}>
                        <th style={{ textAlign: "left", padding: "4px 8px", fontWeight: 400 }}>Type</th>
                        <th style={{ textAlign: "left", padding: "4px 8px", fontWeight: 400 }}>Outcome</th>
                        <th style={{ textAlign: "left", padding: "4px 8px", fontWeight: 400 }}>Notes</th>
                        <th style={{ textAlign: "right", padding: "4px 8px", fontWeight: 400 }}>Date</th>
                      </tr>
                    </thead>
                    <tbody>
                      {pagedActivities.map((a) => (
                        <tr key={a.id} style={{ borderTop: "0.5px solid var(--color-border-tertiary)" }}>
                          <td style={{ padding: "5px 8px" }}><ActivityTypeTag type={a.type} /></td>
                          <td style={{ padding: "5px 8px", color: "var(--color-text-secondary)" }}>{a.outcome || "—"}</td>
                          <td style={{ padding: "5px 8px", maxWidth: 240, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", color: "var(--color-text-secondary)" }}>{a.notes || "—"}</td>
                          <td style={{ textAlign: "right", padding: "5px 8px", color: "var(--color-text-secondary)", fontSize: 10 }}>
                            {a.activity_date ? a.activity_date.slice(0, 10) : "—"}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  <PaginationControls
                    page={safeActivitiesPage}
                    pageSize={activitiesPageSize}
                    totalItems={allActivities.length}
                    onPageChange={setActivitiesPage}
                    onPageSizeChange={(next) => {
                      setActivitiesPageSize(next);
                      setActivitiesPage(1);
                    }}
                  />
                </Card>
              ) : <ErrorMessage message="Could not load activities" />
            )}

            {/* Attainment Forecast */}
            {activeTab === "forecast" && (
              attainLoading ? <Skeleton h={400} /> : (() => {
                const repRow = (attainData?.reps || []).find((r) => r.rep_id === selectedRepId);
                const teamSummary = attainData?.team_summary || {};
                const quarter = attainData?.quarter || "";
                const daysLeft = (attainData?.days_in_quarter || 0) - (attainData?.days_into_quarter || 0);
                if (!repRow) return <div style={{ color: "var(--color-text-secondary)", fontSize: 13 }}>Forecast data unavailable for this rep.</div>;

                const fc = repRow.forecast || {};
                const gap = repRow.pipeline_gap || {};
                const basePct = fc.base_pct || 0;
                const commitPct = fc.commit_pct || 0;
                const upsidePct = fc.upside_pct || 0;
                const gaugeFill = Math.min(100, basePct);

                const MOMENTUM_COLORS = { accelerating: "#1D9E75", steady_up: "#59C099", stable: "#EF9F27", slowing: "#F97316", at_risk: "#D85A30", new_rep: "#8B5CF6" };
                const MOTIVATION_BG = { on_track: "#D1FAE5", close: "#FEF3C7", building: "#DBEAFE", needs_focus: "#FEF3C7", at_risk: "#FEE2E2" };
                const MOTIVATION_COLOR = { on_track: "#065F46", close: "#78350F", building: "#1E40AF", needs_focus: "#78350F", at_risk: "#7F1D1D" };

                const focusDeals = repRow.top_focus_deals || [];

                return (
                  <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>

                    {/* Motivational banner */}
                    <div style={{
                      padding: "10px 14px", borderRadius: 8, fontSize: 12, fontWeight: 500,
                      background: MOTIVATION_BG[repRow.motivation_label] || "#F3F4F6",
                      color: MOTIVATION_COLOR[repRow.motivation_label] || "#374151",
                    }}>
                      {repRow.motivation_msg}
                    </div>

                    {/* Top metric row */}
                    <div style={{ display: "grid", gridTemplateColumns: "repeat(4,1fr)", gap: 10 }}>
                      <MetricCard
                        label="QTD Attainment"
                        value={`${repRow.attainment_qtd_pct}%`}
                        color={repRow.attainment_qtd_pct >= 100 ? "#1D9E75" : repRow.attainment_qtd_pct >= 70 ? "#EF9F27" : "#D85A30"}
                        sub={`${daysLeft}d left in ${quarter}`}
                      />
                      <MetricCard label="Commit (P20)" value={`${commitPct}%`} sub="Conservative floor" />
                      <MetricCard
                        label="Base (P50)"
                        value={`${basePct}%`}
                        color={basePct >= 100 ? "#1D9E75" : basePct >= 80 ? "#EF9F27" : "#D85A30"}
                        sub="Most likely outcome"
                      />
                      <MetricCard label="Upside (P80)" value={`${upsidePct}%`} color="#1D9E75" sub="If pipeline converts" />
                    </div>

                    {/* Attainment gauge bar */}
                    <Card>
                      <SectionTitle>Attainment forecast — {quarter}</SectionTitle>
                      <div style={{ position: "relative", margin: "8px 0 4px" }}>
                        {/* Background track */}
                        <div style={{ height: 20, background: "var(--color-background-secondary)", borderRadius: 10, overflow: "hidden", position: "relative" }}>
                          {/* Commit band */}
                          <div style={{
                            position: "absolute", left: 0, top: 0, bottom: 0,
                            width: `${Math.min(100, commitPct)}%`,
                            background: "#BFDBFE", borderRadius: "10px 0 0 10px",
                          }} />
                          {/* Base fill */}
                          <div style={{
                            position: "absolute", left: 0, top: 0, bottom: 0,
                            width: `${gaugeFill}%`,
                            background: basePct >= 100 ? "#1D9E75" : basePct >= 80 ? "#EF9F27" : "#378ADD",
                            borderRadius: "10px 0 0 10px", transition: "width 0.5s",
                          }} />
                          {/* Upside marker */}
                          {upsidePct <= 100 && (
                            <div style={{
                              position: "absolute", top: 2, bottom: 2, width: 2,
                              left: `${Math.min(100, upsidePct)}%`,
                              background: "#059669",
                            }} />
                          )}
                          {/* 100% marker */}
                          <div style={{ position: "absolute", top: 0, bottom: 0, width: 2, left: "100%", transform: "translateX(-2px)", background: "#374151" }} />
                        </div>
                        <div style={{ display: "flex", justifyContent: "space-between", marginTop: 4, fontSize: 10, color: "var(--color-text-secondary)" }}>
                          <span>0%</span>
                          <span style={{ color: "#1E40AF", fontWeight: 600 }}>Commit {commitPct}%</span>
                          <span style={{ color: basePct >= 100 ? "#065F46" : "#78350F", fontWeight: 600 }}>Base {basePct}%</span>
                          <span style={{ color: "#065F46", fontWeight: 600 }}>Upside {upsidePct}%</span>
                          <span>100% quota</span>
                        </div>
                      </div>
                      <div style={{ display: "flex", alignItems: "center", gap: 6, marginTop: 6, fontSize: 11 }}>
                        <span style={{ color: "var(--color-text-secondary)" }}>Momentum:</span>
                        <span style={{
                          padding: "2px 8px", borderRadius: 10, fontWeight: 600, fontSize: 10,
                          background: MOMENTUM_COLORS[repRow.momentum] || "#9CA3AF", color: "#fff",
                        }}>
                          {(repRow.momentum || "stable").replace("_", " ")}
                        </span>
                        <span style={{ color: "var(--color-text-secondary)", marginLeft: 8 }}>
                          Peer rank: <strong>#{repRow.peer_rank}</strong> of {repRow.peer_total} · Team avg: <strong>{teamSummary.avg_base_attainment_pct}%</strong>
                        </span>
                      </div>
                    </Card>

                    {/* Pipeline gap */}
                    <Card>
                      <SectionTitle>Pipeline gap analysis</SectionTitle>
                      <div style={{ display: "grid", gridTemplateColumns: "repeat(3,1fr)", gap: 10, marginBottom: 10 }}>
                        <MetricCard
                          label="Quota gap"
                          value={fmt(gap.quota_gap)}
                          sub="Revenue still needed"
                          color={gap.quota_gap > 0 ? "#D85A30" : "#1D9E75"}
                        />
                        <MetricCard
                          label="Expected from pipe"
                          value={fmt(gap.expected_from_pipe)}
                          sub={`${gap.win_rate_pct}% win rate × ${fmt(gap.open_pipeline)} pipe`}
                          color="#378ADD"
                        />
                        <MetricCard
                          label="Additional pipe needed"
                          value={gap.pipe_needed > 0 ? fmt(gap.pipe_needed) : "Covered"}
                          sub={gap.pipe_needed > 0 ? "Add to pipeline to cover gap" : "Pipeline covers quota gap"}
                          color={gap.pipe_needed > 0 ? "#EF9F27" : "#1D9E75"}
                        />
                      </div>
                      <div style={{ fontSize: 11, color: "var(--color-text-secondary)" }}>
                        {gap.pipe_needed > 0
                          ? `You need ${fmt(gap.pipe_needed / Math.max(gap.open_pipeline / Math.max(gap.quota_gap, 1), 1))} in new deals at your current win rate to fully cover quota. Focus on the deals below.`
                          : `Your pipeline covers the quota gap. Maintain close discipline to protect the upside.`}
                      </div>
                    </Card>

                    {/* Focus deals */}
                    {focusDeals.length > 0 && (
                      <Card>
                        <SectionTitle>Top focus deals this quarter</SectionTitle>
                        <div style={{ fontSize: 11, color: "var(--color-text-secondary)", marginBottom: 8 }}>
                          Ranked by weighted value (amount × win probability). These are your highest-leverage closes.
                        </div>
                        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                          {focusDeals.map((d, i) => (
                            <div key={d.deal_id} style={{
                              display: "flex", alignItems: "center", gap: 10,
                              padding: "8px 10px", borderRadius: 8,
                              background: "var(--color-background-secondary)",
                              border: "1px solid var(--color-border-secondary)",
                            }}>
                              <div style={{
                                width: 22, height: 22, borderRadius: "50%", display: "flex", alignItems: "center", justifyContent: "center",
                                background: ["#378ADD", "#1D9E75", "#EF9F27"][i] || "#ccc", color: "#fff", fontSize: 11, fontWeight: 700, flexShrink: 0,
                              }}>{i + 1}</div>
                              <div style={{ flex: 1, minWidth: 0 }}>
                                <div style={{ fontSize: 12, fontWeight: 600, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{d.name}</div>
                                <div style={{ fontSize: 10, color: "var(--color-text-secondary)" }}>
                                  <span style={{ padding: "1px 6px", borderRadius: 8, background: STAGE_COLORS[d.stage] || "#ccc", color: d.stage.includes("Closed") ? "#fff" : "#333", marginRight: 6 }}>{d.stage}</span>
                                  Close: {d.expected_close_date || "—"}
                                </div>
                              </div>
                              <div style={{ textAlign: "right", flexShrink: 0 }}>
                                <div style={{ fontSize: 13, fontWeight: 700 }}>{fmt(d.amount)}</div>
                                <div style={{ fontSize: 10, color: "var(--color-text-secondary)" }}>
                                  {d.close_probability}% win · <span style={{ color: "#1D9E75", fontWeight: 600 }}>{fmt(d.weighted_value)} expected</span>
                                </div>
                              </div>
                            </div>
                          ))}
                        </div>
                      </Card>
                    )}

                    {/* Team context */}
                    <Card>
                      <SectionTitle>Team context — {quarter}</SectionTitle>
                      <div style={{ display: "grid", gridTemplateColumns: "repeat(3,1fr)", gap: 10 }}>
                        <MetricCard label="Team avg forecast" value={`${teamSummary.avg_base_attainment_pct}%`} sub="Base attainment" />
                        <MetricCard label="On track" value={teamSummary.on_track_count} sub="Reps projected ≥100%" color="#1D9E75" />
                        <MetricCard label="At risk" value={teamSummary.at_risk_count} sub="Reps projected <70%" color="#D85A30" />
                      </div>
                    </Card>

                    {attainData?.warnings?.length > 0 && (
                      <div style={{ fontSize: 11, color: "var(--color-text-secondary)", fontStyle: "italic" }}>
                        {attainData.warnings[0]}
                      </div>
                    )}
                  </div>
                );
              })()
            )}

            {/* Quota / commission history */}
            {activeTab === "quota" && (
              stmtLoading ? <Skeleton h={300} /> :
              stmtData ? (
                <div>
                  {(() => {
                    const statementRows = [...(stmtData.statements || [])].sort((a, b) => String(b.period).localeCompare(String(a.period)));
                    const nonZeroBonus = statementRows.filter((s) => Number(s.bonus || 0) > 0).length;
                    const nonZeroAccel = statementRows.filter((s) => Number(s.accelerator || 0) > 0).length;
                    return (
                      <>
                  <div style={{ display: "grid", gridTemplateColumns: "repeat(3,1fr)", gap: 10, marginBottom: "1rem" }}>
                    <MetricCard
                      label="Avg Attainment"
                      value={pct(statementRows.length ? statementRows.reduce((sum, s) => sum + Number(s.attainment_pct || 0), 0) / statementRows.length : 0)}
                      sub="Last 12 periods"
                    />
                    <MetricCard
                      label="Avg Monthly Payout"
                      value={fmt(statementRows.length ? statementRows.reduce((sum, s) => sum + Number(s.total_payout || 0), 0) / statementRows.length : 0)}
                      sub="Commission + accelerator + bonus"
                    />
                    <MetricCard
                      label="Reconciliation"
                      value={`${statementRows.filter((s) => Math.abs((Number(s.commission || 0) + Number(s.accelerator || 0) + Number(s.bonus || 0)) - Number(s.total_payout || 0)) < 0.01).length}/${statementRows.length || 0}`}
                      sub="Rows where payout math checks out"
                    />
                  </div>
                  {(nonZeroBonus === 0 || nonZeroAccel === 0) && (
                    <div style={{ fontSize: 11, color: "var(--color-text-secondary)", marginBottom: "0.7rem" }}>
                      Incentive diagnostics: accelerator triggered in {nonZeroAccel}/{statementRows.length} periods and bonus in {nonZeroBonus}/{statementRows.length} periods.
                    </div>
                  )}
                  <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10, marginBottom: "1rem" }}>
                    <Card>
                      <SectionTitle>Attainment over time</SectionTitle>
                      <ResponsiveContainer width="100%" height={180}>
                        <BarChart data={statementRows} margin={{ left: 0 }}>
                          <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border-tertiary)" />
                          <XAxis dataKey="period" tick={{ fontSize: 10 }} tickFormatter={(v) => v.slice(5)} />
                          <YAxis tick={{ fontSize: 11 }} tickFormatter={(v) => `${v}%`} />
                          <Tooltip formatter={(v) => `${v.toFixed(1)}%`} />
                          <Bar dataKey="attainment_pct" radius={[4, 4, 0, 0]}>
                            {statementRows.map((s) => (
                              <Cell key={s.period} fill={s.attainment_pct >= 100 ? "#1D9E75" : s.attainment_pct >= 80 ? "#EF9F27" : "#D85A30"} />
                            ))}
                          </Bar>
                        </BarChart>
                      </ResponsiveContainer>
                    </Card>
                    <Card>
                      <SectionTitle>Payout over time</SectionTitle>
                      <ResponsiveContainer width="100%" height={180}>
                        <BarChart data={statementRows} margin={{ left: 0 }}>
                          <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border-tertiary)" />
                          <XAxis dataKey="period" tick={{ fontSize: 10 }} tickFormatter={(v) => v.slice(5)} />
                          <YAxis tick={{ fontSize: 11 }} tickFormatter={(v) => `$${(v / 1000).toFixed(0)}K`} />
                          <Tooltip formatter={(v, n) => n === "Commission" || n === "Accelerator" || n === "Bonus" ? fmt(v) : String(v)} />
                          <Legend wrapperStyle={{ fontSize: 11 }} />
                          <Bar dataKey="commission" name="Commission" stackId="payout" fill="#378ADD" />
                          <Bar dataKey="accelerator" name="Accelerator" stackId="payout" fill="#1D9E75" />
                          <Bar dataKey="bonus" name="Bonus" stackId="payout" fill="#EF9F27" radius={[4, 4, 0, 0]} />
                        </BarChart>
                      </ResponsiveContainer>
                    </Card>
                  </div>
                  <Card>
                    <SectionTitle>Commission statements</SectionTitle>
                    <div style={{ fontSize: 11, color: "var(--color-text-secondary)", marginBottom: 8 }}>
                      Check column validates that total payout matches the sum of commission, accelerator, and bonus. Incentive Notes explain why accelerator/bonus may remain zero.
                    </div>
                    <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
                      <thead>
                        <tr style={{ color: "var(--color-text-secondary)", fontSize: 11 }}>
                          {["Period", "Revenue", "Quota", "Attainment", "Commission", "Accelerator", "Bonus", "Incentive Notes", "Total", "Check"].map((h) => (
                            <th key={h} style={{ textAlign: h === "Period" ? "left" : "right", padding: "4px 8px", fontWeight: 400 }}>{h}</th>
                          ))}
                        </tr>
                      </thead>
                      <tbody>
                        {statementRows.map((s) => {
                          const reconciled = Math.abs((Number(s.commission || 0) + Number(s.accelerator || 0) + Number(s.bonus || 0)) - Number(s.total_payout || 0)) < 0.01;
                          const incentiveNote = Number(s.accelerator || 0) > 0 || Number(s.bonus || 0) > 0
                            ? "Triggered"
                            : Number(s.attainment_pct || 0) < 100
                              ? "Below 100% attainment"
                              : "No bonus threshold hit";
                          return (
                          <tr key={s.period} style={{ borderTop: "0.5px solid var(--color-border-tertiary)" }}>
                            <td style={{ padding: "5px 8px" }}>{s.period}</td>
                            <td style={{ textAlign: "right", padding: "5px 8px" }}>{fmt(s.revenue)}</td>
                            <td style={{ textAlign: "right", padding: "5px 8px" }}>{fmt(s.quota)}</td>
                            <td style={{ textAlign: "right", padding: "5px 8px", color: s.attainment_pct >= 100 ? "#1D9E75" : "#D85A30" }}>{pct(s.attainment_pct)}</td>
                            <td style={{ textAlign: "right", padding: "5px 8px" }}>{fmt(s.commission)}</td>
                            <td style={{ textAlign: "right", padding: "5px 8px" }}>{fmt(s.accelerator)}</td>
                            <td style={{ textAlign: "right", padding: "5px 8px", color: s.bonus > 0 ? "#1D9E75" : undefined }}>{fmt(s.bonus)}</td>
                            <td style={{ textAlign: "right", padding: "5px 8px", fontSize: 10, color: incentiveNote === "Triggered" ? "#1D9E75" : "var(--color-text-secondary)" }}>{incentiveNote}</td>
                            <td style={{ textAlign: "right", padding: "5px 8px", fontWeight: 600 }}>{fmt(s.total_payout)}</td>
                            <td style={{ textAlign: "right", padding: "5px 8px", color: reconciled ? "#1D9E75" : "#D85A30", fontWeight: 500 }}>{reconciled ? "OK" : "Check"}</td>
                          </tr>
                        )})}
                      </tbody>
                    </table>
                  </Card>
                      </>
                    );
                  })()}
                </div>
              ) : <ErrorMessage message="Could not load statements" />
            )}
          </>
        )}
      </div>
    </div>
  );
}
