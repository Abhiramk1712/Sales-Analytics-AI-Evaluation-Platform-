import { useMemo, useState, useEffect } from "react";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { PaginationControls } from "../components/shared";
import { useFetch } from "../hooks/useFetch";
import { pickDefaultPlan } from "../utils/format";

const API = import.meta.env.VITE_API_URL || "";
const fmt = (n) => n >= 1e6 ? `$${(n/1e6).toFixed(1)}M` : n >= 1e3 ? `$${(n/1e3).toFixed(0)}K` : `$${Number(n||0).toFixed(0)}`;
const pct = (n) => `${Number(n||0).toFixed(1)}%`;

function Skeleton({ h = 120 }) {
  return <div style={{ background: "var(--color-background-secondary)", borderRadius: 8, height: h, animation: "pulse 1.5s infinite" }} />;
}

// useFetch is imported from ../hooks/useFetch. This file used to define its
// own, taking only a url and therefore sending neither X-User-Role nor
// X-Company-Id — so every request here fell back to DEMO_DEFAULT_COMPANY and
// showed the default tenant whatever the selector said.

function StatCard({ label, value, sub, accent = "#378ADD" }) {
  return (
    <div style={{ border: "0.5px solid var(--color-border-tertiary)", borderRadius: 10, padding: "12px 14px", background: "var(--color-background-primary)", boxShadow: "0 2px 8px rgba(12,23,34,0.04)" }}>
      <div style={{ fontSize: 10, color: "var(--color-text-secondary)", textTransform: "uppercase", letterSpacing: "0.5px", marginBottom: 6 }}>{label}</div>
      <div style={{ fontSize: 22, fontWeight: 600, color: accent }}>{value}</div>
      {sub && <div style={{ marginTop: 4, fontSize: 11, color: "var(--color-text-secondary)" }}>{sub}</div>}
    </div>
  );
}

function planNarrative(planPerf) {
  const attainment = Number(planPerf?.attainment_pct || 0);
  if (attainment >= 110) {
    return "Plan is outperforming target. Keep upside incentives but monitor margin exposure and rule overlap.";
  }
  if (attainment >= 90) {
    return "Plan is near target. Tighten rep coaching in the 85-100% band and simplify exception-heavy rules.";
  }
  if (attainment > 0) {
    return "Plan is under target. Review territory-fit, quota pressure, and threshold realism for next cycle.";
  }
  return "No measurable performance yet. Confirm assignments and period coverage before making plan adjustments.";
}

function AuditDrawer({ trace, onClose }) {
  if (!trace) return null;
  return (
    <div style={{ position: "fixed", right: 0, top: 0, bottom: 0, width: 380, background: "var(--color-background-primary)", borderLeft: "1px solid var(--color-border-secondary)", zIndex: 100, padding: 16, overflowY: "auto" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
        <div style={{ fontWeight: 600, fontSize: 14 }}>Audit Trail</div>
        <button onClick={onClose} style={{ border: "none", background: "none", cursor: "pointer", fontSize: 16 }}>×</button>
      </div>
      {Array.isArray(trace) ? (
        <ol style={{ paddingLeft: 18, fontSize: 12, color: "var(--color-text-secondary)", lineHeight: 1.7 }}>
          {trace.map((step, i) => <li key={i}>{step}</li>)}
        </ol>
      ) : (
        <pre style={{ fontSize: 11, whiteSpace: "pre-wrap" }}>{JSON.stringify(trace, null, 2)}</pre>
      )}
    </div>
  );
}

export default function PlansPage({ refreshKey, userRole, activeCompany }) {
  const [selectedPlan, setSelectedPlan] = useState(null);
  const [auditTrace, setAuditTrace] = useState(null);
  const [assignmentPage, setAssignmentPage] = useState(1);
  const [assignmentPageSize, setAssignmentPageSize] = useState(10);
  const [rulePage, setRulePage] = useState(1);
  const [rulePageSize, setRulePageSize] = useState(10);
  const [planSearch, setPlanSearch] = useState("");

  const companyQuery = encodeURIComponent(activeCompany || "");
  const planFetchMarker = `_r=${refreshKey}&company=${companyQuery}`;
  const { data: plans, loading, error: plansError } = useFetch(`/plans?_r=${refreshKey}&company=${companyQuery}`);
  const planRows = plans?.plans || [];
  const activePlanId = useMemo(() => {
    if (selectedPlan && planRows.some((p) => p.id === selectedPlan)) {
      return selectedPlan;
    }
    return pickDefaultPlan(planRows)?.id || null;
  }, [selectedPlan, planRows]);

  const { data: planDetail, loading: detailLoading, error: detailError } = useFetch(activePlanId ? `/plans/${activePlanId}?${planFetchMarker}` : null);
  const { data: planRules } = useFetch(activePlanId ? `/plans/${activePlanId}/rules?${planFetchMarker}` : null);
  const { data: planAssignments } = useFetch(activePlanId ? `/plans/${activePlanId}/assignments?${planFetchMarker}` : null);
  const { data: planPerf } = useFetch(activePlanId ? `/plans/${activePlanId}/performance?${planFetchMarker}` : null);
  const { data: companyRules } = useFetch(`/plans/rules/all?_r=${refreshKey}&company=${companyQuery}`);

  useEffect(() => {
    setSelectedPlan(null);
    setAuditTrace(null);
    setAssignmentPage(1);
    setRulePage(1);
    setPlanSearch("");
  }, [refreshKey, activeCompany]);

  useEffect(() => {
    if (activePlanId !== selectedPlan) {
      setSelectedPlan(activePlanId);
    }
  }, [activePlanId, selectedPlan]);

  const canEdit = ["revops_admin", "finance_admin"].includes(userRole);
  const planNameById = useMemo(() => {
    const map = {};
    planRows.forEach((p) => {
      map[p.id] = p.name;
    });
    return map;
  }, [planRows]);

  const companyRuleRows = (companyRules?.rules || []).map((r) => ({
    ...r,
    plan_name: planNameById[r.plan_id] || "Plan",
  }));
  const assignments = planAssignments?.assignments || [];
  const assignmentPages = Math.max(1, Math.ceil(assignments.length / Math.max(1, assignmentPageSize)));
  const safeAssignmentPage = Math.min(assignmentPage, assignmentPages);
  const pagedAssignments = assignments.slice((safeAssignmentPage - 1) * assignmentPageSize, safeAssignmentPage * assignmentPageSize);
  const rulePages = Math.max(1, Math.ceil(companyRuleRows.length / Math.max(1, rulePageSize)));
  const safeRulePage = Math.min(rulePage, rulePages);
  const pagedCompanyRules = companyRuleRows.slice((safeRulePage - 1) * rulePageSize, safeRulePage * rulePageSize);

  const filteredPlanRows = useMemo(() => {
    const needle = planSearch.trim().toLowerCase();
    if (!needle) return planRows;
    return planRows.filter((plan) => {
      const hay = `${plan.name || ""} ${plan.scope || ""}`.toLowerCase();
      return hay.includes(needle);
    });
  }, [planRows, planSearch]);

  const ruleChartData = (planRules?.rules || []).map((r) => ({
    name: r.name,
    rate_pct: Number(r.rate || 0) * 100,
  }));

  const selectedPlanMeta = planRows.find((p) => p.id === activePlanId) || null;
  const activeAttainment = Number(planPerf?.attainment_pct || 0);
  const attainmentColor = activeAttainment >= 100 ? "#1D9E75" : activeAttainment >= 80 ? "#EF9F27" : "#D85A30";
  const activeRules = planRules?.rules || [];
  const activeAssignments = planAssignments?.assignments || [];
  const topRules = activeRules.slice(0, 3);

  return (
    <div style={{ display: "grid", gap: 14, position: "relative" }}>
      <div style={{ border: "0.5px solid rgba(255,255,255,0.12)", borderRadius: 12, padding: 16, background: "linear-gradient(132deg, #0E2338 0%, #1A4568 54%, #2A6A93 100%)", color: "#F8FBFF", boxShadow: "0 5px 18px rgba(14,35,56,0.2)" }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", flexWrap: "wrap", gap: 10 }}>
          <div>
            <div style={{ fontSize: 11, textTransform: "uppercase", letterSpacing: "0.6px", opacity: 0.82, marginBottom: 5 }}>Comp Plan Command</div>
            <div style={{ fontSize: 20, fontWeight: 600, marginBottom: 4 }}>{selectedPlanMeta?.name || planDetail?.name || "Plan Insights"}</div>
            <div style={{ fontSize: 12, opacity: 0.9 }}>{activeCompany || "No company selected"}</div>
          </div>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
            <span style={{ fontSize: 11, padding: "3px 9px", borderRadius: 999, background: "#1D9E7533", color: "#8EE7C6" }}>Plans: {planRows.length}</span>
            <span style={{ fontSize: 11, padding: "3px 9px", borderRadius: 999, background: "#378ADD33", color: "#A8CFF4" }}>Rules: {companyRuleRows.length}</span>
            <span style={{ fontSize: 11, padding: "3px 9px", borderRadius: 999, background: "#EF9F2733", color: "#FFD59D" }}>Assignments: {activeAssignments.length}</span>
          </div>
        </div>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(320px, 1fr))", gap: 14, alignItems: "start" }}>
        <div style={{ border: "0.5px solid var(--color-border-tertiary)", borderRadius: 12, padding: 12, background: "linear-gradient(166deg, var(--color-background-secondary), var(--color-background-primary))", height: "fit-content", boxShadow: "0 2px 8px rgba(14,35,56,0.06)" }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 9 }}>
            <div style={{ fontSize: 13, fontWeight: 600 }}>Plan Directory</div>
            <div style={{ fontSize: 11, color: "var(--color-text-secondary)" }}>{filteredPlanRows.length}</div>
          </div>
          <input
            value={planSearch}
            onChange={(e) => setPlanSearch(e.target.value)}
            placeholder="Search plan name or scope"
            style={{ width: "100%", padding: "8px 9px", borderRadius: 7, border: "0.5px solid var(--color-border-secondary)", marginBottom: 9, fontSize: 12, background: "var(--color-background-primary)", color: "var(--color-text-primary)", boxSizing: "border-box" }}
          />

          {loading ? <Skeleton h={280} /> : (
            <div style={{ display: "grid", gap: 5, maxHeight: 540, overflowY: "auto", paddingRight: 2 }}>
              {filteredPlanRows.map((plan) => (
                <button
                  key={plan.id}
                  onClick={() => setSelectedPlan(plan.id)}
                  style={{
                    textAlign: "left",
                    padding: "9px 10px",
                    borderRadius: 8,
                    border: activePlanId === plan.id ? "1px solid #1D9E75" : "0.5px solid var(--color-border-tertiary)",
                    background: activePlanId === plan.id ? "rgba(29,158,117,0.14)" : "var(--color-background-primary)",
                    color: "var(--color-text-primary)",
                    fontSize: 12,
                    cursor: "pointer",
                    fontFamily: "var(--font-sans)",
                  }}
                >
                  <div style={{ fontWeight: 600 }}>{plan.name}</div>
                  <div style={{ marginTop: 3, fontSize: 10, color: "var(--color-text-secondary)" }}>
                    {plan.scope || "Scope N/A"}
                    {" · "}
                    {plan.effective_start_date || "No start date"}
                  </div>
                </button>
              ))}
              {filteredPlanRows.length === 0 && (
                <div style={{ fontSize: 12, color: "var(--color-text-secondary)", padding: "4px 2px" }}>No plans found for this search.</div>
              )}
              {plansError && <div style={{ fontSize: 12, color: "#D85A30", padding: "4px 2px" }}>{plansError}</div>}
            </div>
          )}
        </div>

        <div>
          {!activePlanId ? (
            <div style={{ border: "0.5px solid var(--color-border-tertiary)", borderRadius: 12, padding: 32, textAlign: "center", color: "var(--color-text-secondary)", fontSize: 13, background: "var(--color-background-primary)" }}>
              {planRows.length === 0 ? "No plans found for the selected company." : "Select a plan to view details, rules, and assignments."}
            </div>
          ) : detailLoading ? <Skeleton h={320} /> : detailError ? (
            <div style={{ border: "0.5px solid #D85A30", borderRadius: 12, padding: 16, color: "#D85A30", fontSize: 13, background: "rgba(216,90,48,0.08)" }}>
              Could not load plan detail: {detailError}
            </div>
          ) : (
            <>
              <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(260px, 1fr))", gap: 12, marginBottom: 12 }}>
                <div style={{ border: "0.5px solid var(--color-border-tertiary)", borderRadius: 12, padding: 14, background: "var(--color-background-primary)" }}>
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8, flexWrap: "wrap", marginBottom: 8 }}>
                    <div style={{ fontSize: 11, color: "var(--color-text-secondary)", textTransform: "uppercase", letterSpacing: "0.5px" }}>Plan Brief</div>
                    {canEdit && <span style={{ fontSize: 10, borderRadius: 999, padding: "3px 8px", background: "#1D9E7522", color: "#1D9E75" }}>RevOps edit</span>}
                  </div>
                  <div style={{ fontSize: 17, fontWeight: 600 }}>{planDetail?.name || selectedPlanMeta?.name || "Plan"}</div>
                  <div style={{ fontSize: 12, color: "var(--color-text-secondary)", marginTop: 4, lineHeight: 1.55 }}>{planDetail?.description || "No description available for this plan."}</div>
                  <div style={{ marginTop: 10, fontSize: 11, color: "var(--color-text-secondary)", lineHeight: 1.6 }}>
                    {planNarrative(planPerf)}
                  </div>
                  <div style={{ marginTop: 10, display: "flex", flexWrap: "wrap", gap: 6 }}>
                    <span style={{ fontSize: 11, borderRadius: 999, padding: "4px 9px", border: "0.5px solid var(--color-border-secondary)", color: "var(--color-text-secondary)" }}>
                        {planDetail?.effective_start_date || "—"}{" -> "}{planDetail?.effective_end_date || "Open"}
                    </span>
                    <span style={{ fontSize: 11, borderRadius: 999, padding: "4px 9px", background: "var(--color-background-secondary)", color: "var(--color-text-secondary)" }}>
                      Scope: {planDetail?.scope || selectedPlanMeta?.scope || "N/A"}
                    </span>
                  </div>
                </div>

                <div style={{ display: "grid", gridTemplateColumns: "repeat(2, minmax(0, 1fr))", gap: 8 }}>
                  <StatCard label="Revenue" value={fmt(planPerf?.revenue || 0)} sub="Selected period" accent="#1D9E75" />
                  <StatCard label="Quota" value={fmt(planPerf?.quota || 0)} sub="Plan aggregate" accent="#378ADD" />
                  <StatCard label="Attainment" value={pct(activeAttainment)} sub="Revenue / quota" accent={attainmentColor} />
                  <StatCard label="Assigned reps" value={String(planPerf?.rep_count || activeAssignments.length || 0)} sub="Current assignments" accent="#0E4C92" />
                </div>
              </div>

              <div style={{ border: "0.5px solid var(--color-border-tertiary)", borderRadius: 12, padding: 14, marginBottom: 12, background: "var(--color-background-primary)" }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8, flexWrap: "wrap", marginBottom: 10 }}>
                  <div style={{ fontSize: 13, fontWeight: 600 }}>Commission Rules</div>
                  <div style={{ fontSize: 11, color: "var(--color-text-secondary)" }}>{activeRules.length} active rules</div>
                </div>
                {activeRules.length === 0 ? (
                  <div style={{ fontSize: 12, color: "var(--color-text-secondary)" }}>No rules defined for this plan.</div>
                ) : (
                  <>
                    <div style={{ width: "100%", height: 210, marginBottom: 10 }}>
                      <ResponsiveContainer>
                        <BarChart data={ruleChartData} margin={{ left: 0, right: 12, top: 8, bottom: 0 }}>
                          <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border-tertiary)" />
                          <XAxis dataKey="name" tick={{ fontSize: 10 }} />
                          <YAxis tick={{ fontSize: 10 }} tickFormatter={(v) => `${v}%`} />
                          <Tooltip formatter={(v) => `${Number(v || 0).toFixed(2)}%`} />
                          <Bar dataKey="rate_pct" fill="#378ADD" radius={[4, 4, 0, 0]} />
                        </BarChart>
                      </ResponsiveContainer>
                    </div>
                    {topRules.length > 0 && (
                      <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginBottom: 10 }}>
                        {topRules.map((rule) => (
                          <span key={rule.id} style={{ fontSize: 11, borderRadius: 999, padding: "4px 9px", border: "0.5px solid var(--color-border-secondary)", background: "var(--color-background-secondary)", color: "var(--color-text-secondary)" }}>
                            {rule.name}: {pct((rule.rate || 0) * 100)}
                          </span>
                        ))}
                      </div>
                    )}
                    <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
                      <thead>
                        <tr style={{ color: "var(--color-text-secondary)", fontSize: 10, textTransform: "uppercase", letterSpacing: "0.4px" }}>
                          <th style={{ textAlign: "left", padding: "4px 8px", fontWeight: 400 }}>Rule</th>
                          <th style={{ textAlign: "left", padding: "4px 8px", fontWeight: 400 }}>Metric</th>
                          <th style={{ textAlign: "right", padding: "4px 8px", fontWeight: 400 }}>Min</th>
                          <th style={{ textAlign: "right", padding: "4px 8px", fontWeight: 400 }}>Max</th>
                          <th style={{ textAlign: "right", padding: "4px 8px", fontWeight: 400 }}>Rate</th>
                          <th style={{ textAlign: "right", padding: "4px 8px", fontWeight: 400 }}>Bonus</th>
                        </tr>
                      </thead>
                      <tbody>
                        {activeRules.map((rule) => (
                          <tr key={rule.id} style={{ borderTop: "0.5px solid var(--color-border-tertiary)" }}>
                            <td style={{ padding: "8px 8px" }}>{rule.name}</td>
                            <td style={{ padding: "8px 8px", color: "var(--color-text-secondary)" }}>{rule.metric_name || "—"}</td>
                            <td style={{ padding: "8px 8px", textAlign: "right" }}>{rule.threshold_min ?? "—"}%</td>
                            <td style={{ padding: "8px 8px", textAlign: "right" }}>{rule.threshold_max ?? "—"}%</td>
                            <td style={{ padding: "8px 8px", textAlign: "right", fontWeight: 500 }}>{rule.rate != null ? pct(rule.rate * 100) : "—"}</td>
                            <td style={{ padding: "8px 8px", textAlign: "right" }}>{rule.bonus_amount ? fmt(rule.bonus_amount) : "—"}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </>
                )}
              </div>

              <div style={{ border: "0.5px solid var(--color-border-tertiary)", borderRadius: 12, padding: 14, marginBottom: 12, background: "var(--color-background-primary)" }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8, flexWrap: "wrap", marginBottom: 10 }}>
                  <div style={{ fontSize: 13, fontWeight: 600 }}>Assigned Reps Summary</div>
                  <div style={{ fontSize: 11, color: "var(--color-text-secondary)" }}>{assignments.length} assignments</div>
                </div>
                {activeAssignments.length === 0 ? (
                  <div style={{ fontSize: 12, color: "var(--color-text-secondary)" }}>No users assigned.</div>
                ) : (
                  <>
                    <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
                      <thead>
                        <tr style={{ color: "var(--color-text-secondary)", fontSize: 10, textTransform: "uppercase", letterSpacing: "0.4px" }}>
                          <th style={{ textAlign: "left", padding: "4px 8px", fontWeight: 400 }}>Rep</th>
                          <th style={{ textAlign: "left", padding: "4px 8px", fontWeight: 400 }}>Start</th>
                          <th style={{ textAlign: "left", padding: "4px 8px", fontWeight: 400 }}>End</th>
                          <th style={{ textAlign: "right", padding: "4px 8px", fontWeight: 400 }}>Details</th>
                        </tr>
                      </thead>
                      <tbody>
                        {pagedAssignments.map((assignment) => (
                          <tr key={assignment.assignment_id} style={{ borderTop: "0.5px solid var(--color-border-tertiary)" }}>
                            <td style={{ padding: "8px 8px" }}>{assignment.user_name || assignment.user_id?.slice(0, 8) || "—"}</td>
                            <td style={{ padding: "8px 8px" }}>{assignment.effective_start_date || "—"}</td>
                            <td style={{ padding: "8px 8px" }}>{assignment.effective_end_date || "Open"}</td>
                            <td style={{ textAlign: "right", padding: "8px 8px" }}>
                              <button
                                onClick={() => setAuditTrace(assignment)}
                                style={{ border: "0.5px solid var(--color-border-secondary)", background: "var(--color-background-secondary)", borderRadius: 6, padding: "2px 8px", cursor: "pointer", fontSize: 11 }}
                              >
                                View
                              </button>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                    <PaginationControls
                      page={safeAssignmentPage}
                      pageSize={assignmentPageSize}
                      totalItems={assignments.length}
                      onPageChange={setAssignmentPage}
                      onPageSizeChange={(next) => {
                        setAssignmentPageSize(next);
                        setAssignmentPage(1);
                      }}
                    />
                  </>
                )}
              </div>

              <div style={{ border: "0.5px solid var(--color-border-tertiary)", borderRadius: 12, padding: 14, background: "var(--color-background-primary)" }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8, flexWrap: "wrap", marginBottom: 10 }}>
                  <div style={{ fontSize: 13, fontWeight: 600 }}>Company-Level Rules Across Plans</div>
                  <div style={{ fontSize: 11, color: "var(--color-text-secondary)" }}>{companyRuleRows.length} total rules</div>
                </div>
                {(companyRules?.rules || []).length === 0 ? (
                  <div style={{ fontSize: 12, color: "var(--color-text-secondary)" }}>No rules found across plans.</div>
                ) : (
                  <>
                    <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
                      <thead>
                        <tr style={{ color: "var(--color-text-secondary)", fontSize: 10, textTransform: "uppercase", letterSpacing: "0.4px" }}>
                          <th style={{ textAlign: "left", padding: "4px 8px", fontWeight: 400 }}>Plan</th>
                          <th style={{ textAlign: "left", padding: "4px 8px", fontWeight: 400 }}>Rule</th>
                          <th style={{ textAlign: "right", padding: "4px 8px", fontWeight: 400 }}>Rate</th>
                          <th style={{ textAlign: "right", padding: "4px 8px", fontWeight: 400 }}>Band</th>
                        </tr>
                      </thead>
                      <tbody>
                        {pagedCompanyRules.map((rule) => (
                          <tr key={rule.id} style={{ borderTop: "0.5px solid var(--color-border-tertiary)" }}>
                            <td style={{ padding: "8px 8px" }}>{rule.plan_name}</td>
                            <td style={{ padding: "8px 8px" }}>{rule.name}</td>
                            <td style={{ textAlign: "right", padding: "8px 8px" }}>{pct((rule.rate || 0) * 100)}</td>
                            <td style={{ textAlign: "right", padding: "8px 8px" }}>{rule.threshold_min ?? "-"}% - {rule.threshold_max ?? "-"}%</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                    <PaginationControls
                      page={safeRulePage}
                      pageSize={rulePageSize}
                      totalItems={companyRuleRows.length}
                      onPageChange={setRulePage}
                      onPageSizeChange={(next) => {
                        setRulePageSize(next);
                        setRulePage(1);
                      }}
                    />
                  </>
                )}
              </div>
            </>
          )}
        </div>
      </div>

      {auditTrace && <AuditDrawer trace={auditTrace} onClose={() => setAuditTrace(null)} />}
    </div>
  );
}
