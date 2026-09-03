/**
 * PayoutAuditPage.jsx — the payout lifecycle/approval queue, wired to
 * /payout-audit/* (backend/routers/payout_audit.py).
 *
 * This is the front door backend/routers/payout_audit.py never had: real
 * lifecycle logic (draft -> reviewed -> approved -> locked -> paid, with
 * adjusted as a correction reachable from any state) existed and was tested
 * before this page did, reachable only via /docs or curl. See
 * docs/PAYOUT_AUDIT_TRAIL.md.
 */
import { useMemo, useState } from "react";
import { useFetch } from "../hooks/useFetch";
import { useUrlState } from "../hooks/useUrlState";
import { apiPost } from "../api/client";
import { Card, SectionTitle, MetricCard, StatusBadge, Skeleton, ErrorMessage, EmptyState } from "../components/shared";
import { fmt, pct, withRefresh } from "../utils/format";

const LIFECYCLE_STATES = ["draft", "reviewed", "approved", "locked", "paid", "adjusted"];

const STATE_BADGE = {
  draft:    "neutral",
  reviewed: "info",
  approved: "medium",
  locked:   "warning",
  paid:     "success",
  adjusted: "info",
};

// Which action buttons apply to a row, in lifecycle order. `adjust` has no
// lock guard on the backend by design (corrections must reach a payout in
// any state, including paid) so it's always offered.
function actionsFor(state) {
  const actions = [];
  if (state === "draft") actions.push("review");
  if (state === "draft" || state === "reviewed") actions.push("approve");
  if (state === "approved") actions.push("lock");
  if (state === "locked") actions.push("pay");
  actions.push("adjust");
  return actions;
}

const ACTION_LABEL = {
  review: "Mark reviewed",
  approve: "Approve",
  lock: "Lock",
  pay: "Mark paid",
  adjust: "Adjust",
};

function money(v) {
  return fmt(Number(v || 0));
}

// Every trace value seen in practice is a string, number, boolean, or array
// of strings — except adjustment_amount's "adjustment" entry, a nested
// {amount, reason, actor, at} object. Rendering that with String(value) prints
// "[object Object]"; this renders any plain object as its own key:value list
// instead of assuming the shape is always flat.
function formatTraceValue(value) {
  if (Array.isArray(value)) return value.length ? value.join("; ") : "—";
  if (value !== null && typeof value === "object") {
    return Object.entries(value).map(([k, v]) => `${k.replace(/_/g, " ")}: ${v}`).join(", ");
  }
  return String(value ?? "—");
}

function TraceList({ title, trace }) {
  const entries = trace && typeof trace === "object" ? Object.entries(trace) : [];
  if (entries.length === 0) {
    return (
      <div style={{ fontSize: 12, color: "var(--color-text-tertiary)", fontStyle: "italic" }}>
        No {title.toLowerCase()} recorded for this row — it was loaded from persisted payout
        history rather than a live calculation run, so no audit trace exists for it yet.
      </div>
    );
  }
  return (
    <div style={{ display: "grid", gap: 4 }}>
      {entries.map(([key, value]) => (
        <div key={key} style={{ display: "flex", gap: 8, fontSize: 12 }}>
          <span style={{ color: "var(--color-text-secondary)", minWidth: 140, fontWeight: 600 }}>{key.replace(/_/g, " ")}</span>
          <span style={{ color: "var(--color-text-primary)", wordBreak: "break-word" }}>
            {formatTraceValue(value)}
          </span>
        </div>
      ))}
    </div>
  );
}

function CriticalIssuesNotice({ issues, onDismiss }) {
  return (
    <div style={{ padding: "12px 16px", background: "var(--color-red-light)", border: "1px solid #fecaca", borderRadius: "var(--border-radius-md)", marginBottom: 12 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 12 }}>
        <div>
          <div style={{ fontWeight: 700, fontSize: 13, color: "var(--color-red)", marginBottom: 6 }}>
            Approval blocked — critical data quality issues exist
          </div>
          <ul style={{ margin: 0, paddingLeft: 18, fontSize: 12, color: "var(--color-red)" }}>
            {issues.map((iss, i) => (
              <li key={i}>{iss.name || iss.check_name || JSON.stringify(iss)}{iss.severity ? ` (${iss.severity})` : ""}</li>
            ))}
          </ul>
          <div style={{ fontSize: 11, color: "var(--color-text-secondary)", marginTop: 6 }}>
            Resolve these under Data Quality before approving this payout.
          </div>
        </div>
        <button onClick={onDismiss} style={{ background: "none", border: "none", color: "var(--color-red)", fontSize: 16, cursor: "pointer", lineHeight: 1 }}>×</button>
      </div>
    </div>
  );
}

function PayoutRow({ row, role, company, canAct, expanded, onToggleExpand, onActed }) {
  const [pendingAction, setPendingAction] = useState(null); // "approve" | "lock" | "adjust" | null
  const [note, setNote] = useState("");
  const [adjustAmount, setAdjustAmount] = useState("");
  const [adjustReason, setAdjustReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [rowError, setRowError] = useState("");
  const [criticalIssues, setCriticalIssues] = useState(null);

  const runAction = async (action, body) => {
    setBusy(true);
    setRowError("");
    setCriticalIssues(null);
    try {
      await apiPost(`/payout-audit/${row.payout_id}/${action}`, body || {}, { role, company });
      setPendingAction(null);
      setNote("");
      setAdjustAmount("");
      setAdjustReason("");
      onActed();
    } catch (e) {
      // apiPost stringifies a structured `detail` object; approve's blocked-by-
      // data-quality response needs the real critical_issues array, so this
      // one action re-fetches the raw response instead of trusting the
      // already-stringified Error.
      if (action === "approve") {
        try {
          const API = import.meta.env.VITE_API_URL || "";
          const res = await fetch(`${API}/payout-audit/${row.payout_id}/approve`, {
            method: "POST",
            headers: { "Content-Type": "application/json", ...(role ? { "X-User-Role": role } : {}), ...(company ? { "X-Company-Id": company } : {}) },
            body: JSON.stringify(body || {}),
          });
          const parsed = await res.json().catch(() => ({}));
          if (res.status === 409 && parsed?.detail?.critical_issues) {
            setCriticalIssues(parsed.detail.critical_issues);
            setBusy(false);
            return;
          }
        } catch { /* fall through to generic error below */ }
      }
      setRowError(e.message || "Action failed");
    } finally {
      setBusy(false);
    }
  };

  const available = actionsFor(row.lifecycle_state);

  return (
    <>
      <tr style={{ borderTop: "0.5px solid var(--color-border-tertiary)" }}>
        <td style={{ padding: "8px 10px" }}>
          <button onClick={onToggleExpand} style={{ background: "none", border: "none", cursor: "pointer", fontSize: 11, color: "var(--color-text-secondary)", padding: 0 }}>
            {expanded ? "▾" : "▸"}
          </button>
        </td>
        <td style={{ padding: "8px 10px", fontWeight: 500 }}>
          {row.rep_name || row.source_records_json?.rep_name || row.rep_id || "—"}
        </td>
        <td style={{ padding: "8px 10px" }}>{row.period}</td>
        <td style={{ padding: "8px 10px", textAlign: "right" }}>{pct(row.attainment_pct)}</td>
        <td style={{ padding: "8px 10px", textAlign: "right", fontWeight: 600 }}>{money(row.final_payout)}</td>
        <td style={{ padding: "8px 10px" }}><StatusBadge status={STATE_BADGE[row.lifecycle_state] || "neutral"} label={row.lifecycle_state} /></td>
        <td style={{ padding: "8px 10px", fontSize: 11, color: "var(--color-text-secondary)" }}>
          {row.fallback_used ? "Fallback" : "Direct"}
        </td>
        <td style={{ padding: "8px 10px" }}>
          {canAct ? (
            <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
              {available.map((action) => (
                <button
                  key={action}
                  disabled={busy}
                  onClick={() => {
                    if (action === "lock" || action === "adjust" || action === "approve") {
                      setPendingAction(pendingAction === action ? null : action);
                    } else {
                      runAction(action);
                    }
                  }}
                  style={{
                    fontSize: 11, padding: "4px 9px", borderRadius: "var(--border-radius-sm)",
                    border: "1px solid var(--color-border-secondary)",
                    background: pendingAction === action ? "var(--color-background-tertiary)" : "var(--color-background-primary)",
                    color: "var(--color-text-primary)", cursor: busy ? "wait" : "pointer",
                  }}
                >
                  {ACTION_LABEL[action]}
                </button>
              ))}
            </div>
          ) : (
            <span style={{ fontSize: 11, color: "var(--color-text-tertiary)" }}>View only</span>
          )}
        </td>
      </tr>

      {(pendingAction || rowError || criticalIssues) && (
        <tr>
          <td colSpan={8} style={{ padding: "0 10px 10px 10px", background: "var(--color-background-secondary)" }}>
            {criticalIssues && <CriticalIssuesNotice issues={criticalIssues} onDismiss={() => setCriticalIssues(null)} />}
            {rowError && <div style={{ marginBottom: 8 }}><ErrorMessage message={rowError} /></div>}

            {pendingAction === "approve" && (
              <div style={{ display: "flex", gap: 8, alignItems: "center", padding: "8px 0" }}>
                <input
                  placeholder="Optional approval note"
                  value={note}
                  onChange={(e) => setNote(e.target.value)}
                  style={{ flex: 1, fontSize: 12, padding: "6px 8px", borderRadius: "var(--border-radius-sm)", border: "1px solid var(--color-border-secondary)" }}
                />
                <button disabled={busy} onClick={() => runAction("approve", { note: note || null })} style={primaryBtnStyle}>Confirm approve</button>
                <button onClick={() => setPendingAction(null)} style={ghostBtnStyle}>Cancel</button>
              </div>
            )}

            {pendingAction === "lock" && (
              <div style={{ display: "flex", gap: 8, alignItems: "center", padding: "8px 0" }}>
                <span style={{ fontSize: 12, color: "var(--color-text-secondary)" }}>
                  Locking stops further recalculation of this payout — corrections after this point go through Adjust. Confirm?
                </span>
                <button disabled={busy} onClick={() => runAction("lock")} style={{ ...primaryBtnStyle, background: "var(--color-amber)" }}>Confirm lock</button>
                <button onClick={() => setPendingAction(null)} style={ghostBtnStyle}>Cancel</button>
              </div>
            )}

            {pendingAction === "adjust" && (
              <div style={{ display: "flex", gap: 8, alignItems: "center", padding: "8px 0", flexWrap: "wrap" }}>
                <input
                  type="number"
                  placeholder="Adjustment amount (+/-)"
                  value={adjustAmount}
                  onChange={(e) => setAdjustAmount(e.target.value)}
                  style={{ width: 170, fontSize: 12, padding: "6px 8px", borderRadius: "var(--border-radius-sm)", border: "1px solid var(--color-border-secondary)" }}
                />
                <input
                  placeholder="Reason (required)"
                  value={adjustReason}
                  onChange={(e) => setAdjustReason(e.target.value)}
                  style={{ flex: 1, minWidth: 200, fontSize: 12, padding: "6px 8px", borderRadius: "var(--border-radius-sm)", border: "1px solid var(--color-border-secondary)" }}
                />
                <button
                  disabled={busy || !adjustAmount || adjustReason.trim().length < 3}
                  onClick={() => runAction("adjust", { adjustment_amount: Number(adjustAmount), reason: adjustReason.trim() })}
                  style={primaryBtnStyle}
                >
                  Confirm adjustment
                </button>
                <button onClick={() => setPendingAction(null)} style={ghostBtnStyle}>Cancel</button>
              </div>
            )}
          </td>
        </tr>
      )}

      {expanded && (
        <tr>
          <td colSpan={8} style={{ padding: "10px 16px 16px 34px", background: "var(--color-background-secondary)" }}>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 20 }}>
              <div>
                <div style={{ fontSize: 11, fontWeight: 700, color: "var(--color-text-secondary)", marginBottom: 6, textTransform: "uppercase", letterSpacing: "0.4px" }}>Calculation trace</div>
                <TraceList title="Calculation trace" trace={row.calculation_trace_json} />
              </div>
              <div>
                <div style={{ fontSize: 11, fontWeight: 700, color: "var(--color-text-secondary)", marginBottom: 6, textTransform: "uppercase", letterSpacing: "0.4px" }}>Source records</div>
                <TraceList title="Source records" trace={row.source_records_json} />
              </div>
            </div>
            <div style={{ display: "flex", gap: 16, marginTop: 12, fontSize: 11, color: "var(--color-text-secondary)" }}>
              <span>Base: <strong style={{ color: "var(--color-text-primary)" }}>{money(row.base_commission)}</strong></span>
              <span>Accelerator: <strong style={{ color: "var(--color-text-primary)" }}>{money(row.accelerator_amount)}</strong></span>
              <span>SPIFF: <strong style={{ color: "var(--color-text-primary)" }}>{money(row.spiff_amount)}</strong></span>
              <span>Clawback: <strong style={{ color: "var(--color-text-primary)" }}>{money(row.clawback_amount)}</strong></span>
              <span>Confidence: <strong style={{ color: "var(--color-text-primary)" }}>{
                (() => {
                  // DB-fallback rows carry confidence top-level; real audit-trail
                  // rows (from upsert_payout_trace) only have it inside the trace.
                  const c = row.confidence ?? row.calculation_trace_json?.confidence;
                  return c != null ? pct(Number(c) * 100) : "—";
                })()
              }</strong></span>
              {row.approved_by && <span>Approved by: <strong style={{ color: "var(--color-text-primary)" }}>{row.approved_by}</strong></span>}
              {row.locked_at && <span>Locked at: <strong style={{ color: "var(--color-text-primary)" }}>{new Date(row.locked_at).toLocaleString()}</strong></span>}
            </div>
          </td>
        </tr>
      )}
    </>
  );
}

const primaryBtnStyle = {
  fontSize: 12, padding: "6px 12px", borderRadius: "var(--border-radius-sm)",
  border: "none", background: "var(--color-green)", color: "#fff", fontWeight: 600, cursor: "pointer",
};
const ghostBtnStyle = {
  fontSize: 12, padding: "6px 12px", borderRadius: "var(--border-radius-sm)",
  border: "1px solid var(--color-border-secondary)", background: "transparent",
  color: "var(--color-text-secondary)", cursor: "pointer",
};

export default function PayoutAuditPage({ refreshKey, activeCompany, userRole }) {
  const role = userRole || "executive";
  const company = activeCompany || "";
  const canAct = role === "revops_admin" || role === "finance_admin";

  const [filterState, setFilterState] = useUrlState({ approvalFilter: "" });
  const stateFilter = filterState.approvalFilter;

  const [localRefresh, setLocalRefresh] = useState(0);
  const [expandedId, setExpandedId] = useState(null);

  const url = withRefresh(
    `/payout-audit${stateFilter ? `?lifecycle_state=${encodeURIComponent(stateFilter)}` : ""}`,
    refreshKey + localRefresh,
  );
  const { data, loading, error } = useFetch(url, { role, company });

  const rows = data?.rows || [];

  const summary = useMemo(() => {
    const totalPayout = rows.reduce((sum, r) => sum + Number(r.final_payout || 0), 0);
    const byState = {};
    for (const state of LIFECYCLE_STATES) byState[state] = 0;
    for (const r of rows) byState[r.lifecycle_state] = (byState[r.lifecycle_state] || 0) + 1;
    return { totalPayout, byState, count: rows.length };
  }, [rows]);

  return (
    <div>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 16 }}>
        <SectionTitle sub="The payout audit trail's lifecycle actions — approve, lock, mark paid, or issue a correction. Read-only for roles without approve_payouts.">
          Payout Approvals
        </SectionTitle>
        <select
          value={stateFilter}
          onChange={(e) => setFilterState({ approvalFilter: e.target.value })}
          style={{ fontSize: 12, padding: "6px 10px", borderRadius: "var(--border-radius-sm)", border: "1px solid var(--color-border-secondary)", background: "var(--color-background-primary)", color: "var(--color-text-primary)" }}
        >
          <option value="">All states</option>
          {LIFECYCLE_STATES.map((s) => <option key={s} value={s}>{s}</option>)}
        </select>
      </div>

      {loading && <Skeleton h={360} />}
      {error && <ErrorMessage message={error} />}

      {!loading && !error && (
        <>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(6,1fr)", gap: 10, marginBottom: 18 }}>
            <MetricCard label="Records" value={summary.count} sub={stateFilter || "all states"} />
            <MetricCard label="Total payout" value={money(summary.totalPayout)} color="#1D9E75" />
            {["draft", "approved", "locked", "paid", "adjusted"].map((s) => (
              <MetricCard key={s} label={s} value={summary.byState[s] || 0} />
            ))}
          </div>

          <Card>
            {rows.length === 0 ? (
              <EmptyState
                icon="🧾"
                title="No payout records"
                message={stateFilter ? `Nothing in the "${stateFilter}" state for this company.` : "No payout audit records yet — visit Payouts to run a calculation."}
              />
            ) : (
              <div style={{ overflowX: "auto" }}>
                <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
                  <thead>
                    <tr style={{ color: "var(--color-text-secondary)", fontSize: 11, textTransform: "uppercase", letterSpacing: "0.4px" }}>
                      {["", "Rep", "Period", "Attainment", "Final payout", "State", "Source", "Actions"].map((h) => (
                        <th key={h} style={{ textAlign: h === "Final payout" || h === "Attainment" ? "right" : "left", padding: "6px 10px", fontWeight: 400 }}>{h}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((row) => (
                      <PayoutRow
                        key={row.payout_id}
                        row={row}
                        role={role}
                        company={company}
                        canAct={canAct}
                        expanded={expandedId === row.payout_id}
                        onToggleExpand={() => setExpandedId(expandedId === row.payout_id ? null : row.payout_id)}
                        onActed={() => setLocalRefresh((n) => n + 1)}
                      />
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Card>
        </>
      )}
    </div>
  );
}
