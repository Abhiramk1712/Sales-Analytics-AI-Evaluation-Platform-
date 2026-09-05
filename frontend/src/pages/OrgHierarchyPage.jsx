/**
 * OrgHierarchyPage.jsx
 * ────────────────────
 * Shows:
 *  1. Rank ladder — visual tier breakdown of executive/vp/director/manager/ic
 *  2. Org tree — manager chain with rank badges per node
 *  3. Plan cascade rules — which plans flow from which executives downward
 *  4. Position rank table — all positions and their assigned ranks
 */
import { useState, useEffect } from "react";

const API = import.meta.env.VITE_API_URL || "";

// ── Rank config ─────────────────────────────────────────────────────────────
const RANK_CONFIG = [
  { rank: 1, label: "Executive", color: "#185FA5", bg: "rgba(24,95,165,0.10)", icon: "★" },
  { rank: 2, label: "VP",        color: "#378ADD", bg: "rgba(55,138,221,0.10)", icon: "◆" },
  { rank: 3, label: "Director",  color: "#1D9E75", bg: "rgba(29,158,117,0.10)", icon: "▲" },
  { rank: 4, label: "Manager",   color: "#EF9F27", bg: "rgba(239,159,39,0.10)",  icon: "■" },
  { rank: 5, label: "IC",        color: "#888",    bg: "rgba(136,136,136,0.08)", icon: "●" },
];

function rankConfig(rank) {
  return RANK_CONFIG.find((r) => r.rank === rank) || { rank: 99, label: "Unknown", color: "#888", bg: "#f5f5f5", icon: "?" };
}

function RankBadge({ rank, label }) {
  const cfg = rankConfig(rank);
  return (
    <span style={{
      display: "inline-flex", alignItems: "center", gap: 4,
      padding: "2px 10px", borderRadius: 99, fontSize: 11, fontWeight: 600,
      color: cfg.color, background: cfg.bg, border: `1px solid ${cfg.color}33`,
    }}>
      {cfg.icon} {label || cfg.label}
    </span>
  );
}

// ── Scope badge ──────────────────────────────────────────────────────────────
const SCOPE_CONFIG = {
  global:     { color: "#185FA5", label: "Global" },
  department: { color: "#1D9E75", label: "Department" },
  team:       { color: "#EF9F27", label: "Team" },
  individual: { color: "#888",    label: "Individual" },
};

function ScopeBadge({ scope }) {
  const cfg = SCOPE_CONFIG[scope] || SCOPE_CONFIG.individual;
  return (
    <span style={{
      padding: "2px 8px", borderRadius: 99, fontSize: 11, fontWeight: 500,
      color: cfg.color, background: `${cfg.color}18`, border: `1px solid ${cfg.color}44`,
    }}>
      {cfg.label}
    </span>
  );
}

// ── Rank Ladder ──────────────────────────────────────────────────────────────
function RankLadder({ positions }) {
  const grouped = {};
  (positions || []).forEach((p) => {
    const r = p.rank ?? 99;
    if (!grouped[r]) grouped[r] = [];
    grouped[r].push(p);
  });

  return (
    <div style={{ border: "0.5px solid var(--color-border-tertiary)", borderRadius: "var(--border-radius-lg)", padding: 16 }}>
      <div style={{ fontSize: 13, fontWeight: 500, marginBottom: 14 }}>Position Rank Ladder</div>
      <div style={{ display: "grid", gap: 10 }}>
        {RANK_CONFIG.map(({ rank, label, color, bg }) => {
          const members = grouped[rank] || [];
          return (
            <div key={rank} style={{
              display: "grid", gridTemplateColumns: "140px 1fr", gap: 12, alignItems: "start",
              padding: "10px 12px", borderRadius: "var(--border-radius-md)", background: bg,
              border: `0.5px solid ${color}33`,
            }}>
              <div>
                <div style={{ fontWeight: 600, color, fontSize: 13 }}>Rank {rank} — {label}</div>
                <div style={{ fontSize: 11, color: "var(--color-text-secondary)", marginTop: 2 }}>
                  {members.length} position{members.length !== 1 ? "s" : ""}
                </div>
                <div style={{ marginTop: 6, fontSize: 11, color: "var(--color-text-secondary)", lineHeight: 1.5 }}>
                  {rank === 1 && "Sets global rules. Plans cascade to all reports."}
                  {rank === 2 && "Department-level plans cascade to their division."}
                  {rank === 3 && "Team plans cascade to all direct reports."}
                  {rank === 4 && "Manager-level overrides apply to direct reports only."}
                  {rank === 5 && "Individual contributors. Receive cascaded plans."}
                </div>
              </div>
              <div style={{ display: "flex", flexWrap: "wrap", gap: 6, paddingTop: 2 }}>
                {members.length === 0 && (
                  <span style={{ fontSize: 11, color: "var(--color-text-secondary)", fontStyle: "italic" }}>
                    No positions assigned to this rank
                  </span>
                )}
                {members.map((p) => (
                  <span key={p.id || p.name} style={{
                    padding: "3px 10px", borderRadius: 99, fontSize: 12,
                    background: "var(--color-background-primary)",
                    border: `0.5px solid ${color}55`, color: "var(--color-text-primary)",
                  }}>
                    {p.name}
                    {p.level && <span style={{ fontSize: 10, color: "var(--color-text-secondary)", marginLeft: 4 }}>({p.level})</span>}
                  </span>
                ))}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ── Org Tree node ────────────────────────────────────────────────────────────
function OrgNode({ node, depth = 0 }) {
  const [expanded, setExpanded] = useState(depth < 2);
  const hasChildren = (node.reports || []).length > 0;
  const cfg = rankConfig(node.rank ?? 99);

  return (
    <div style={{ marginLeft: depth * 20 }}>
      <div style={{
        display: "flex", alignItems: "center", gap: 8, padding: "6px 10px",
        borderRadius: "var(--border-radius-md)", marginBottom: 4,
        background: depth === 0 ? cfg.bg : "transparent",
        border: depth === 0 ? `0.5px solid ${cfg.color}33` : "none",
        cursor: hasChildren ? "pointer" : "default",
      }} onClick={() => hasChildren && setExpanded((e) => !e)}>
        {hasChildren && (
          <span style={{ fontSize: 10, color: "var(--color-text-secondary)", width: 12 }}>
            {expanded ? "▾" : "▸"}
          </span>
        )}
        {!hasChildren && <span style={{ width: 12 }} />}
        <div style={{ flex: 1 }}>
          <span style={{ fontSize: 13, fontWeight: depth === 0 ? 600 : 400 }}>{node.name}</span>
          {node.email && (
            <span style={{ fontSize: 11, color: "var(--color-text-secondary)", marginLeft: 8 }}>
              {node.email}
            </span>
          )}
        </div>
        <RankBadge rank={node.rank ?? 99} label={node.rank_label || undefined} />
        {node.plan_count > 0 && (
          <span style={{ fontSize: 11, padding: "1px 7px", borderRadius: 99, background: "rgba(55,138,221,0.1)", color: "#378ADD" }}>
            {node.plan_count} plan{node.plan_count !== 1 ? "s" : ""}
          </span>
        )}
        {node.cascade_rule_count > 0 && (
          <span style={{ fontSize: 11, padding: "1px 7px", borderRadius: 99, background: "rgba(29,158,117,0.1)", color: "#1D9E75" }}>
            {node.cascade_rule_count} cascade
          </span>
        )}
      </div>
      {expanded && hasChildren && (
        <div style={{ borderLeft: `1px solid var(--color-border-tertiary)`, marginLeft: 22, paddingLeft: 8 }}>
          {(node.reports || []).map((child) => (
            <OrgNode key={child.id || child.name} node={child} depth={depth + 1} />
          ))}
        </div>
      )}
    </div>
  );
}

// ── Cascade Rules Table ──────────────────────────────────────────────────────
function CascadeRulesTable({ rules }) {
  if (!rules || rules.length === 0) {
    return (
      <div style={{ fontSize: 12, color: "var(--color-text-secondary)", padding: "12px 0" }}>
        No cascade rules defined. Executive and director users should create cascade rules so
        their plans automatically apply to all reports in their org subtree.
      </div>
    );
  }

  return (
    <div style={{ overflowX: "auto" }}>
      <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
        <thead>
          <tr style={{ borderBottom: "0.5px solid var(--color-border-tertiary)" }}>
            {["Plan", "Scope", "Owned By", "Cascade To", "Rank Range", "Priority", "Effective"].map((h) => (
              <th key={h} style={{
                textAlign: "left", padding: "6px 10px", fontWeight: 500,
                fontSize: 11, color: "var(--color-text-secondary)",
                textTransform: "uppercase", letterSpacing: "0.4px",
              }}>{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rules.map((r, i) => (
            <tr key={i} style={{ borderBottom: "0.5px solid var(--color-border-tertiary)" }}>
              <td style={{ padding: "8px 10px", fontWeight: 500 }}>{r.plan_name || r.plan_id}</td>
              <td style={{ padding: "8px 10px" }}><ScopeBadge scope={r.plan_scope} /></td>
              <td style={{ padding: "8px 10px" }}>
                <div>{r.owner_name || r.owner_user_id}</div>
                {r.owner_rank != null && (
                  <RankBadge rank={r.owner_rank} label={r.owner_rank_label} />
                )}
              </td>
              <td style={{ padding: "8px 10px" }}>
                <span style={{
                  padding: "2px 8px", borderRadius: 99, fontSize: 11,
                  background: r.cascade_scope === "all_reports" ? "rgba(29,158,117,0.1)" : "rgba(239,159,39,0.1)",
                  color: r.cascade_scope === "all_reports" ? "#1D9E75" : "#EF9F27",
                }}>
                  {r.cascade_scope === "all_reports" ? "All reports" : "Direct only"}
                </span>
              </td>
              <td style={{ padding: "8px 10px", color: "var(--color-text-secondary)" }}>
                {r.min_rank}–{r.max_rank}
              </td>
              <td style={{ padding: "8px 10px" }}>
                <span style={{
                  fontWeight: r.priority <= 10 ? 700 : 400,
                  color: r.priority <= 10 ? "#185FA5" : "var(--color-text-primary)",
                }}>
                  {r.priority}
                </span>
                {r.priority <= 10 && (
                  <span style={{ fontSize: 10, color: "#185FA5", marginLeft: 4 }}>global</span>
                )}
              </td>
              <td style={{ padding: "8px 10px", color: "var(--color-text-secondary)", fontSize: 11 }}>
                {r.effective_start_date || "—"} → {r.effective_end_date || "open"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ── Positions table ──────────────────────────────────────────────────────────
function PositionsTable({ positions, activeCompany, userRole }) {
  const [editing, setEditing] = useState(null); // { id, rank, rank_label }
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);

  const handleSave = async () => {
    if (!editing) return;
    setSaving(true);
    setError(null);
    try {
      const res = await fetch(`${API}/analytics/positions/${editing.id}/rank`, {
        method: "PATCH",
        headers: {
          "Content-Type": "application/json",
          ...(userRole ? { "X-User-Role": userRole } : {}),
          ...(activeCompany ? { "X-Company-Id": activeCompany } : {}),
        },
        body: JSON.stringify({ rank: editing.rank, rank_label: editing.rank_label }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || `Save failed (${res.status})`);
      }
      setEditing(null);
    } catch (e) {
      setError(e.message);
    }
    setSaving(false);
  };

  return (
    <div>
      {error && (
        <div style={{ fontSize: 12, color: "#D85A30", marginBottom: 8, padding: "6px 10px", background: "rgba(216,90,48,0.07)", borderRadius: 4 }}>
          {error}
        </div>
      )}
      <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
        <thead>
          <tr style={{ borderBottom: "0.5px solid var(--color-border-tertiary)" }}>
            {["Position Name", "Level", "Rank", "Rank Label", ""].map((h) => (
              <th key={h} style={{
                textAlign: "left", padding: "6px 10px", fontWeight: 500,
                fontSize: 11, color: "var(--color-text-secondary)",
                textTransform: "uppercase", letterSpacing: "0.4px",
              }}>{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {(positions || []).map((p) => {
            const isEditing = editing?.id === p.id;
            return (
              <tr key={p.id} style={{ borderBottom: "0.5px solid var(--color-border-tertiary)" }}>
                <td style={{ padding: "8px 10px", fontWeight: 500 }}>{p.name}</td>
                <td style={{ padding: "8px 10px", color: "var(--color-text-secondary)" }}>{p.level || "—"}</td>
                <td style={{ padding: "8px 10px" }}>
                  {isEditing ? (
                    <select
                      value={editing.rank}
                      onChange={(e) => setEditing((prev) => ({ ...prev, rank: Number(e.target.value) }))}
                      style={{ padding: "4px 6px", fontSize: 12, borderRadius: 4, border: "0.5px solid var(--color-border-secondary)", background: "var(--color-background-primary)" }}
                    >
                      {RANK_CONFIG.map((rc) => (
                        <option key={rc.rank} value={rc.rank}>{rc.rank} — {rc.label}</option>
                      ))}
                      <option value={99}>99 — Unknown</option>
                    </select>
                  ) : (
                    <RankBadge rank={p.rank ?? 99} label={p.rank_label || rankConfig(p.rank ?? 99).label} />
                  )}
                </td>
                <td style={{ padding: "8px 10px" }}>
                  {isEditing ? (
                    <input
                      value={editing.rank_label}
                      onChange={(e) => setEditing((prev) => ({ ...prev, rank_label: e.target.value }))}
                      placeholder={rankConfig(editing.rank).label}
                      style={{ padding: "4px 6px", fontSize: 12, borderRadius: 4, border: "0.5px solid var(--color-border-secondary)", background: "var(--color-background-primary)", width: 120 }}
                    />
                  ) : (
                    <span style={{ color: "var(--color-text-secondary)" }}>{p.rank_label || "—"}</span>
                  )}
                </td>
                <td style={{ padding: "8px 10px" }}>
                  {isEditing ? (
                    <div style={{ display: "flex", gap: 6 }}>
                      <button
                        onClick={handleSave}
                        disabled={saving}
                        style={{ padding: "3px 10px", fontSize: 11, borderRadius: 4, border: "none", background: "#1D9E75", color: "#fff", cursor: "pointer" }}
                      >
                        {saving ? "…" : "Save"}
                      </button>
                      <button
                        onClick={() => { setEditing(null); setError(null); }}
                        style={{ padding: "3px 10px", fontSize: 11, borderRadius: 4, border: "0.5px solid var(--color-border-secondary)", background: "none", cursor: "pointer" }}
                      >
                        Cancel
                      </button>
                    </div>
                  ) : (
                    <button
                      onClick={() => setEditing({ id: p.id, rank: p.rank ?? 99, rank_label: p.rank_label || "" })}
                      style={{ padding: "3px 10px", fontSize: 11, borderRadius: 4, border: "0.5px solid var(--color-border-secondary)", background: "none", cursor: "pointer", color: "var(--color-text-secondary)" }}
                    >
                      Edit rank
                    </button>
                  )}
                </td>
              </tr>
            );
          })}
          {(!positions || positions.length === 0) && (
            <tr><td colSpan={5} style={{ padding: "12px 10px", color: "var(--color-text-secondary)", fontSize: 12 }}>No positions found.</td></tr>
          )}
        </tbody>
      </table>
    </div>
  );
}

// ── Main page ────────────────────────────────────────────────────────────────
export default function OrgHierarchyPage({ refreshKey, activeCompany, userRole }) {
  const [tab, setTab] = useState("ladder");
  const [orgData, setOrgData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [positions, setPositions] = useState(null);
  const [posLoading, setPosLoading] = useState(false);
  const [cascadeRules, setCascadeRules] = useState(null);
  const [rulesLoading, setRulesLoading] = useState(false);

  const _headers = { "Content-Type": "application/json" };
  if (userRole) _headers["X-User-Role"] = userRole;
  if (activeCompany) _headers["X-Company-Id"] = activeCompany;

  // Reset all cached data when company changes
  useEffect(() => {
    setOrgData(null);
    setPositions(null);
    setCascadeRules(null);
    setError(null);
  }, [refreshKey, activeCompany]);

  // Lazy load each tab's data when first selected
  const loadOrg = async () => {
    if (loading) return;
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(`${API}/analytics/manager-tree`, { headers: _headers });
      if (!res.ok) throw new Error(`Failed to load org tree (${res.status})`);
      setOrgData(await res.json());
    } catch (e) {
      setError(e.message);
    }
    setLoading(false);
  };

  const loadPositions = async () => {
    if (posLoading) return;
    setPosLoading(true);
    try {
      const res = await fetch(`${API}/analytics/positions`, { headers: _headers });
      if (!res.ok) throw new Error(`Failed to load positions (${res.status})`);
      const body = await res.json();
      setPositions(body.positions || body || []);
    } catch {
      setPositions([]);
    }
    setPosLoading(false);
  };

  const loadCascadeRules = async () => {
    if (rulesLoading) return;
    setRulesLoading(true);
    try {
      const res = await fetch(`${API}/analytics/plan-cascade-rules`, { headers: _headers });
      if (!res.ok) throw new Error(`Failed to load cascade rules (${res.status})`);
      const body = await res.json();
      setCascadeRules(body.rules || body || []);
    } catch {
      setCascadeRules([]);
    }
    setRulesLoading(false);
  };

  const handleTab = (t) => {
    setTab(t);
    if (t === "ladder" || t === "positions") loadPositions();
    if (t === "org")    loadOrg();
    if (t === "rules")  loadCascadeRules();
  };

  // Pre-load ladder data on mount and on company change
  useEffect(() => { loadPositions(); }, [refreshKey]); // eslint-disable-line react-hooks/exhaustive-deps

  const TABS = [
    { key: "ladder",    label: "Rank Ladder" },
    { key: "org",       label: "Org Tree" },
    { key: "rules",     label: "Cascade Rules" },
    { key: "positions", label: "All Positions" },
  ];

  return (
    <div style={{ display: "grid", gap: 12 }}>
      {/* Header */}
      <div style={{ border: "0.5px solid var(--color-border-tertiary)", borderRadius: "var(--border-radius-lg)", padding: "14px 16px" }}>
        <div style={{ fontSize: 15, fontWeight: 600, marginBottom: 4 }}>Org Hierarchy &amp; Plan Cascade</div>
        <div style={{ fontSize: 12, color: "var(--color-text-secondary)", lineHeight: 1.6 }}>
          Positions are ranked 1–5 (lower = higher authority). Plans set by executives
          (rank&nbsp;≤&nbsp;3) cascade automatically to all reports via
          <strong> Plan Cascade Rules</strong>.
          Individual reps receive their plan through: direct assignment &rarr; manager cascade &rarr; fallback engine.
        </div>
      </div>

      {/* Rank tier summary */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(5,1fr)", gap: 8 }}>
        {RANK_CONFIG.map(({ rank, label, color, bg }) => (
          <div key={rank} style={{
            padding: "10px 12px", borderRadius: "var(--border-radius-md)", background: bg,
            border: `0.5px solid ${color}33`, textAlign: "center",
          }}>
            <div style={{ fontSize: 20, marginBottom: 4 }}>{rank === 1 ? "★" : rank === 2 ? "◆" : rank === 3 ? "▲" : rank === 4 ? "■" : "●"}</div>
            <div style={{ fontSize: 12, fontWeight: 600, color }}>{label}</div>
            <div style={{ fontSize: 10, color: "var(--color-text-secondary)", marginTop: 2 }}>Rank {rank}</div>
          </div>
        ))}
      </div>

      {/* Sub-tabs */}
      <div style={{ display: "flex", borderBottom: "0.5px solid var(--color-border-tertiary)" }}>
        {TABS.map(({ key, label }) => (
          <button key={key} onClick={() => handleTab(key)} style={{
            padding: "8px 18px", fontSize: 12, cursor: "pointer", border: "none",
            borderBottom: tab === key ? "2px solid var(--color-text-primary)" : "2px solid transparent",
            background: "none", fontFamily: "var(--font-sans)", marginBottom: -1,
            color: tab === key ? "var(--color-text-primary)" : "var(--color-text-secondary)",
            fontWeight: tab === key ? 500 : 400,
          }}>{label}</button>
        ))}
      </div>

      {/* Tab content */}
      {tab === "ladder" && (
        posLoading
          ? <div style={{ fontSize: 12, color: "var(--color-text-secondary)" }}>Loading positions…</div>
          : <RankLadder positions={positions || []} />
      )}

      {tab === "org" && (
        <div style={{ border: "0.5px solid var(--color-border-tertiary)", borderRadius: "var(--border-radius-lg)", padding: 16 }}>
          <div style={{ fontSize: 13, fontWeight: 500, marginBottom: 12 }}>Reporting Chain</div>
          {loading && <div style={{ fontSize: 12, color: "var(--color-text-secondary)" }}>Loading org tree…</div>}
          {error && <div style={{ fontSize: 12, color: "#D85A30" }}>{error}</div>}
          {!loading && !error && orgData && (() => {
            const roots = orgData.nodes || [];
            if (roots.length === 0) {
              return <div style={{ fontSize: 12, color: "var(--color-text-secondary)" }}>No org data available. Upload a company dataset first.</div>;
            }
            return roots.map((node) => (
              <OrgNode key={node.id || node.name} node={node} depth={0} />
            ));
          })()}
          {!loading && !error && !orgData && (
            <div style={{ fontSize: 12, color: "var(--color-text-secondary)" }}>Click "Org Tree" to load the reporting chain.</div>
          )}
        </div>
      )}

      {tab === "rules" && (
        <div style={{ border: "0.5px solid var(--color-border-tertiary)", borderRadius: "var(--border-radius-lg)", padding: 16 }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
            <div style={{ fontSize: 13, fontWeight: 500 }}>Plan Cascade Rules</div>
            <div style={{ fontSize: 11, color: "var(--color-text-secondary)" }}>
              Rules with priority ≤ 10 are global (set by executives). Lower priority = evaluated first.
            </div>
          </div>
          {rulesLoading
            ? <div style={{ fontSize: 12, color: "var(--color-text-secondary)" }}>Loading cascade rules…</div>
            : <CascadeRulesTable rules={cascadeRules || []} />
          }
        </div>
      )}

      {tab === "positions" && (
        <div style={{ border: "0.5px solid var(--color-border-tertiary)", borderRadius: "var(--border-radius-lg)", padding: 16 }}>
          <div style={{ fontSize: 13, fontWeight: 500, marginBottom: 12 }}>
            All Positions
            <span style={{ fontSize: 11, fontWeight: 400, color: "var(--color-text-secondary)", marginLeft: 8 }}>
              Edit rank to control cascade eligibility
            </span>
          </div>
          {posLoading
            ? <div style={{ fontSize: 12, color: "var(--color-text-secondary)" }}>Loading positions…</div>
            : <PositionsTable positions={positions || []} activeCompany={activeCompany} userRole={userRole} />
          }
        </div>
      )}
    </div>
  );
}
