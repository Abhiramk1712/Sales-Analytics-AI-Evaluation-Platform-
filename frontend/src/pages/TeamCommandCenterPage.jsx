/**
 * TeamCommandCenterPage.jsx — "Team Command Center": the Sales Manager role's home.
 *
 * Replaces the generic Dashboard tab for sales_manager with a team-scoped
 * view: attainment vs. team quota, a quota streak, a coaching queue sorted
 * by risk (not name), pipeline coverage, and the same milestones reps see
 * on their own home page -- worth calling out in standup.
 *
 * "Team" here is the Team model (region-named: "West Sales Team", etc.),
 * not the Manager/Position reporting hierarchy that /analytics/reps/leadership
 * rolls up -- that hierarchy's rollup deliberately excludes "Management"-level
 * positions (i.e. the Sales Manager position itself), so it has no row for
 * an actual sales manager to view. There's also no per-user login in this
 * demo, so -- same as the rep and rep-scorecard pages -- a team picker lets
 * you choose which team's command center to view, defaulting to the
 * highest-revenue team.
 *
 * No new backend endpoint: everything here comes from what the Reps/Forecast
 * tabs already call (/analytics/org-structure for team rosters,
 * /analytics/reps/performance for period-scoped financials,
 * /ml/forecast/rep-attainment for risk/momentum signals), scoped down to one
 * team's roster client-side. Pipeline coverage uses the same ratio
 * backend/statistics/pipeline_health.py already defines (open pipeline over
 * one quarter's quota) -- that code isn't wired into any endpoint, but its
 * definition and thresholds are the project's own, not invented here.
 */
import { useEffect, useMemo, useState } from "react";
import { Card, MetricCard, Skeleton, ErrorMessage, StatusBadge, EmptyState } from "../components/shared";
import { useFetch } from "../hooks/useFetch";
import { apiGet } from "../api/client";
import { fmt, pct, withRefresh, withPeriod, toPayoutPeriod } from "../utils/format";

function initials(name) {
  return (name || "?")
    .split(" ")
    .filter(Boolean)
    .slice(0, 2)
    .map((s) => s[0].toUpperCase())
    .join("");
}

function previousQuarterKey(key) {
  const m = /^(\d{4})-Q([1-4])$/.exec(key || "");
  if (!m) return null;
  let year = Number(m[1]);
  let q = Number(m[2]) - 1;
  if (q < 1) {
    q = 4;
    year -= 1;
  }
  return `${year}-Q${q}`;
}

function lastNQuarterKeys(currentKey, n) {
  const keys = [];
  let k = currentKey;
  for (let i = 0; i < n && k; i++) {
    keys.unshift(k);
    k = previousQuarterKey(k);
  }
  return keys;
}

// Tiering is deliberately NOT based on /ml/forecast/rep-attainment's own
// base_pct/motivation_label/risk_flag: its heuristic fallback (used whenever
// there isn't enough history to fit the GBR model -- true for every company
// in this demo) computes attain = qtd_attainment + pipeline_coverage *
// win_rate with no discount for time left in the quarter, so a rep with a
// large-but-early-stage pipeline gets clamped straight to the 500% cap
// (backend/ml/attainment_forecaster.py:130) regardless of actual attainment.
// Confirmed live: three reps at 60.5% / 80.7% / 155.9% real QTD attainment
// all forecast base_pct=500 and motivation_label="on_track". Tiering here
// instead uses paced_attainment_pct, a plain deterministic extrapolation
// (revenue/quota * days_in_quarter/days_into_quarter, forecasting.py) with
// no such multiplier, plus the rep's real QTD attainment_pct.
function riskTier(item) {
  if (item.paced_pct == null) return "unknown";
  if (item.paced_pct < 70) return "critical";
  if (item.paced_pct < 100) return "watch";
  return "on_track";
}

function coachingMessage(item) {
  if (item.paced_pct == null) return "No forecast signal yet for this rep.";
  const coverageText = item.coverage !== null ? ` Pipeline coverage is ${item.coverage.toFixed(1)}× this period's quota.` : "";
  if (item.tier === "critical") {
    return `Projected to land at ${pct(item.paced_pct)} at the current pace, even with the quarter mostly run.${coverageText}`;
  }
  if (item.tier === "watch") {
    const gap = Math.max(0, Number(item.quota || 0) - Number(item.revenue || 0));
    return `On pace for ${pct(item.paced_pct)}. Needs ${fmt(gap)} more to reach quota.${coverageText}`;
  }
  return `On pace for ${pct(item.paced_pct)}.`;
}

const TIER_ORDER = { critical: 0, watch: 1, unknown: 2, on_track: 3 };
const TIER_META = {
  critical: { status: "error", label: "CRITICAL" },
  watch: { status: "warning", label: "WATCH" },
  on_track: { status: "success", label: "ON TRACK" },
  unknown: { status: "neutral", label: "NO SIGNAL" },
};

export default function TeamCommandCenterPage({ refreshKey, period, userRole, activeCompany }) {
  const role = userRole || "sales_manager";
  const company = activeCompany || "";
  const [selectedTeamId, setSelectedTeamId] = useState(null);

  const { data: orgData, loading: orgLoading, error: orgError } = useFetch(
    withRefresh("/analytics/org-structure", refreshKey),
    { role, company }
  );

  // org-structure buckets a team's members by each member's own territory
  // assignment, not by team -- the same team_id can (and does, in this
  // dataset) appear under several territory buckets, each holding only a
  // slice of the team's roster. Aggregating by team_id and merging members
  // is required; a naive flatten-then-find would silently use just the
  // first fragment (confirmed live: "Central Sales Team" split 1+1 across
  // two territory buckets instead of showing as one team of 2).
  const teams = useMemo(() => {
    const territories = orgData?.territories || [];
    const byId = new Map();
    for (const t of territories) {
      for (const team of t.teams || []) {
        const existing = byId.get(team.team_id);
        if (existing) {
          const seen = new Set(existing.members.map((m) => m.rep_id));
          for (const m of team.members || []) {
            if (!seen.has(m.rep_id)) {
              seen.add(m.rep_id);
              existing.members.push(m);
            }
          }
        } else {
          byId.set(team.team_id, {
            team_id: team.team_id,
            team_name: team.team_name,
            region: team.region,
            members: [...(team.members || [])],
          });
        }
      }
    }
    return [...byId.values()].sort(
      (a, b) => b.members.reduce((s, m) => s + Number(m.revenue || 0), 0) - a.members.reduce((s, m) => s + Number(m.revenue || 0), 0)
    );
  }, [orgData]);

  useEffect(() => {
    setSelectedTeamId(null);
  }, [refreshKey]);
  useEffect(() => {
    if (selectedTeamId || !teams.length) return;
    setSelectedTeamId(teams[0].team_id);
  }, [selectedTeamId, teams]);

  const selectedTeam = useMemo(
    () => teams.find((t) => t.team_id === selectedTeamId) || null,
    [teams, selectedTeamId]
  );
  const rosterIds = useMemo(
    () => new Set((selectedTeam?.members || []).map((m) => m.rep_id)),
    [selectedTeam]
  );
  // A plain string so the streak effect below can depend on roster *content*
  // rather than this Set's object identity -- org-structure refetches (and
  // gets a new object reference) on every role switch even though role
  // doesn't change its response, which otherwise retriggered the effect's
  // 8 quarterly fetches on every company/role change instead of only on an
  // actual roster change (confirmed live: the same quarter fetched 3x on a
  // single company+role switch).
  const rosterKey = useMemo(() => [...rosterIds].sort().join(","), [rosterIds]);

  const { data: perfData, loading: perfLoading, error: perfError } = useFetch(
    withPeriod(withRefresh("/analytics/reps/performance", refreshKey), period),
    { role, company }
  );
  const teamPerf = useMemo(
    () => (perfData || []).filter((r) => rosterIds.has(r.rep_id)),
    [perfData, rosterIds]
  );

  const { data: forecastData, loading: forecastLoading } = useFetch(
    withRefresh("/ml/forecast/rep-attainment", refreshKey),
    { role, company }
  );
  const forecastByRep = useMemo(() => {
    const map = {};
    for (const r of forecastData?.reps || []) map[r.rep_id] = r;
    return map;
  }, [forecastData]);

  const teamRevenue = teamPerf.reduce((s, r) => s + Number(r.revenue || 0), 0);
  const teamQuota = teamPerf.reduce((s, r) => s + Number(r.quota || 0), 0);
  const teamAttainmentPct = teamQuota > 0 ? (teamRevenue / teamQuota) * 100 : 0;
  const teamOpenPipeline = teamPerf.reduce((s, r) => s + Number(r.open_pipeline || 0), 0);
  const pipelineCoverage = teamQuota > 0 ? teamOpenPipeline / teamQuota : null;

  const queue = useMemo(() => {
    return teamPerf
      .map((r) => {
        const fc = forecastByRep[r.rep_id] || null;
        const quota = Number(r.quota || 0);
        const coverage = quota > 0 ? Number(r.open_pipeline || 0) / quota : null;
        const item = {
          rep_id: r.rep_id,
          name: r.name,
          attainment_pct: Number(r.attainment_pct || 0),
          win_rate: Number(r.win_rate || 0),
          revenue: Number(r.revenue || 0),
          quota,
          coverage,
          paced_pct: fc?.paced_attainment_pct ?? null,
        };
        const tier = riskTier(item);
        return { ...item, tier, message: coachingMessage({ ...item, tier }) };
      })
      .sort((a, b) => {
        if (TIER_ORDER[a.tier] !== TIER_ORDER[b.tier]) return TIER_ORDER[a.tier] - TIER_ORDER[b.tier];
        return a.attainment_pct - b.attainment_pct;
      });
  }, [teamPerf, forecastByRep]);

  const critical = queue.filter((q) => q.tier === "critical");
  const watch = queue.filter((q) => q.tier === "watch");
  const criticalWatch = [...critical, ...watch];
  const onTrack = queue.filter((q) => q.tier === "on_track");
  const noSignal = queue.filter((q) => q.tier === "unknown");
  const condensedList = [...onTrack, ...noSignal];

  const badges = useMemo(() => {
    if (!teamPerf.length) return [];
    const list = [];
    const seen = new Set();
    const add = (rep, label, detail) => {
      const key = `${rep.rep_id}:${label}`;
      if (seen.has(key)) return;
      seen.add(key);
      list.push({ rep_id: rep.rep_id, name: rep.name, label, detail });
    };

    const topRevenue = [...teamPerf].sort((a, b) => Number(b.revenue || 0) - Number(a.revenue || 0))[0];
    if (topRevenue) add(topRevenue, "Top Performer", `${fmt(topRevenue.revenue)} revenue`);

    for (const r of teamPerf) {
      if (list.length >= 4) break;
      if (Number(r.attainment_pct || 0) >= 110) add(r, "On Fire", `${pct(r.attainment_pct)} of quota`);
    }

    const topWinRate = [...teamPerf].sort((a, b) => Number(b.win_rate || 0) - Number(a.win_rate || 0))[0];
    if (topWinRate && Number(topWinRate.win_rate || 0) >= 70) {
      add(topWinRate, "Win Rate Leader", `${pct(topWinRate.win_rate)} win rate`);
    }

    return list.slice(0, 4);
  }, [teamPerf]);

  const [streak, setStreak] = useState(null);
  useEffect(() => {
    if (!rosterIds.size) {
      setStreak(null);
      return;
    }
    let cancelled = false;
    const resolved = toPayoutPeriod(period);
    const currentQuarter = /^\d{4}-Q[1-4]$/.test(resolved || "") ? resolved : toPayoutPeriod("this quarter");
    const quarters = lastNQuarterKeys(currentQuarter, 8).filter(Boolean);

    Promise.all(
      quarters.map((q) =>
        apiGet("/analytics/reps/performance", { role, company, params: { period: q } }).catch(() => [])
      )
    ).then((results) => {
      if (cancelled) return;
      const history = quarters.map((q, i) => {
        const rows = (results[i] || []).filter((r) => rosterIds.has(r.rep_id));
        const rev = rows.reduce((s, r) => s + Number(r.revenue || 0), 0);
        const quota = rows.reduce((s, r) => s + Number(r.quota || 0), 0);
        return { quarter: q, attainment_pct: quota > 0 ? (rev / quota) * 100 : 0 };
      });
      let s = 0;
      for (let i = history.length - 1; i >= 0; i--) {
        if (history[i].attainment_pct >= 100) s++;
        else break;
      }
      setStreak(s);
    });

    return () => {
      cancelled = true;
    };
  }, [rosterKey, role, company, period, refreshKey]);

  const loading = orgLoading || perfLoading || forecastLoading;

  if (orgError) return <ErrorMessage message={orgError} />;
  if (!orgLoading && !teams.length) return <EmptyState title="No teams found" message="This company has no teams set up yet." />;

  return (
    <div style={{ display: "grid", gap: 16 }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", flexWrap: "wrap", gap: 10 }}>
        <div>
          <div style={{ fontSize: 20, fontWeight: 800, letterSpacing: "-0.4px" }}>
            {selectedTeam?.team_name || "Team"}
          </div>
          <div style={{ fontSize: 12.5, color: "var(--color-text-secondary)", marginTop: 3 }}>
            {teamPerf.length} rep{teamPerf.length === 1 ? "" : "s"}
            {selectedTeam?.region ? ` · ${selectedTeam.region}` : ""} · {period}
          </div>
        </div>
        {teams.length > 1 && (
          <select
            value={selectedTeamId || ""}
            onChange={(e) => setSelectedTeamId(e.target.value)}
            style={{
              padding: "8px 12px",
              borderRadius: 8,
              border: "1px solid var(--color-border-secondary)",
              fontSize: 13,
              fontWeight: 500,
              background: "var(--color-background-primary)",
              color: "var(--color-text-primary)",
            }}
          >
            {teams.map((t) => (
              <option key={t.team_id} value={t.team_id}>
                {t.team_name}
              </option>
            ))}
          </select>
        )}
      </div>

      {perfError && <ErrorMessage message={perfError} />}

      {loading ? (
        <Skeleton h={280} />
      ) : teamPerf.length === 0 ? (
        <Card>
          <EmptyState
            title="No quota-carrying reps"
            message="This team has no reps with quota data for the selected period."
          />
        </Card>
      ) : (
        <>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))", gap: 12 }}>
            <MetricCard
              label="Team Attainment"
              value={pct(teamAttainmentPct)}
              trend={teamAttainmentPct >= 100 ? "up" : teamAttainmentPct < 80 ? "down" : undefined}
              sub={`${fmt(teamRevenue)} of ${fmt(teamQuota)}`}
            />
            <MetricCard
              label="Team Streak"
              value={streak === null ? "…" : `${streak} quarter${streak === 1 ? "" : "s"}`}
              color="var(--color-amber)"
              sub="At or above team quota"
            />
            <MetricCard
              label="Reps On Track"
              value={`${onTrack.length} of ${queue.length}`}
              color={criticalWatch.length > 0 ? "var(--color-amber)" : "var(--color-green)"}
              sub={criticalWatch.length > 0 ? `${criticalWatch.length} need attention this week` : "Everyone on pace"}
            />
            <MetricCard
              label="Pipeline Coverage"
              value={pipelineCoverage === null ? "—" : `${pipelineCoverage.toFixed(1)}×`}
              sub="Healthy ≥ 2× this period's quota"
            />
          </div>

          <Card>
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 4 }}>
              <div style={{ fontSize: 13, fontWeight: 700 }}>Coaching queue</div>
              <div style={{ fontSize: 11, color: "var(--color-text-tertiary)", fontWeight: 600 }}>sorted by risk, not name</div>
            </div>
            <div style={{ fontSize: 11, color: "var(--color-text-tertiary)", marginBottom: 16 }}>
              Reps who need a conversation this week, first
            </div>
            <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
              {criticalWatch.map((item) => (
                <CoachingRow key={item.rep_id} item={item} />
              ))}
              {criticalWatch.length > 0 && condensedList.length > 0 && (
                <div
                  style={{
                    height: 1,
                    background: "var(--color-border-secondary)",
                    margin: "6px 0",
                  }}
                />
              )}
              {condensedList.length > 0 && (
                <div
                  style={{
                    fontSize: 10.5,
                    fontWeight: 700,
                    color: "var(--color-text-tertiary)",
                    textTransform: "uppercase",
                    letterSpacing: "0.5px",
                    padding: "0 4px",
                  }}
                >
                  On track — {onTrack.length} rep{onTrack.length === 1 ? "" : "s"}
                  {noSignal.length > 0 ? `, ${noSignal.length} no forecast signal yet` : ""}
                </div>
              )}
              {condensedList.map((item) => (
                <OnTrackRow key={item.rep_id} item={item} />
              ))}
            </div>
          </Card>

          <Card>
            <div style={{ fontSize: 13, fontWeight: 700, marginBottom: 3 }}>Team recognition this quarter</div>
            <div style={{ fontSize: 11, color: "var(--color-text-tertiary)", marginBottom: 14 }}>
              Same milestones your reps see — worth calling out in standup
            </div>
            {badges.length === 0 ? (
              <div style={{ fontSize: 12, color: "var(--color-text-secondary)" }}>No milestones earned yet this period.</div>
            ) : (
              <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
                {badges.map((b, i) => (
                  <div
                    key={i}
                    style={{
                      display: "flex",
                      alignItems: "center",
                      gap: 8,
                      padding: "8px 12px",
                      borderRadius: 999,
                      background: "var(--color-amber-light)",
                      border: "1px solid var(--color-amber)",
                    }}
                  >
                    <span style={{ fontSize: 12, fontWeight: 700 }}>{b.name}</span>
                    <span style={{ fontSize: 11.5, color: "var(--color-text-secondary)" }}>
                      — {b.label}, {b.detail}
                    </span>
                  </div>
                ))}
              </div>
            )}
          </Card>
        </>
      )}
    </div>
  );
}

function CoachingRow({ item }) {
  const meta = TIER_META[item.tier];
  const accent = item.tier === "critical" ? "var(--color-red)" : "var(--color-amber)";
  const bg = item.tier === "critical" ? "var(--color-red-light)" : "var(--color-amber-light)";
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: 14,
        padding: "13px 14px",
        borderRadius: 10,
        background: bg,
        border: `1px solid ${accent}`,
      }}
    >
      <Avatar name={item.name} />
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontSize: 13, fontWeight: 700 }}>{item.name}</div>
        <div style={{ fontSize: 11.5, color: "var(--color-text-secondary)", marginTop: 2 }}>
          {item.message}
        </div>
      </div>
      <div style={{ width: 120 }}>
        <div style={{ display: "flex", justifyContent: "flex-end", fontSize: 11, fontWeight: 700, marginBottom: 4 }}>
          <span>{pct(item.attainment_pct)}</span>
        </div>
        <div style={{ height: 6, borderRadius: 999, background: "var(--color-background-tertiary)" }}>
          <div
            style={{
              width: `${Math.min(100, item.attainment_pct)}%`,
              height: "100%",
              borderRadius: 999,
              background: accent,
            }}
          />
        </div>
      </div>
      <StatusBadge status={meta.status} label={meta.label} size="lg" />
    </div>
  );
}

function OnTrackRow({ item }) {
  const noSignal = item.tier === "unknown";
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: 14,
        padding: "8px 14px",
        borderRadius: 10,
        background: "var(--color-background-secondary)",
      }}
    >
      <Avatar name={item.name} size={26} />
      <div style={{ flex: 1, fontSize: 12.5, fontWeight: 600 }}>{item.name}</div>
      <div style={{ fontSize: 12, fontWeight: 700, color: noSignal ? "var(--color-text-tertiary)" : "var(--color-green)" }}>
        {pct(item.attainment_pct)}
      </div>
    </div>
  );
}

function Avatar({ name, size = 36 }) {
  return (
    <div
      style={{
        width: size,
        height: size,
        borderRadius: "50%",
        flexShrink: 0,
        background: "var(--color-background-primary)",
        border: "1.5px solid var(--color-border-secondary)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        fontSize: size > 30 ? 12 : 10,
        fontWeight: 700,
        color: "var(--color-text-secondary)",
      }}
    >
      {initials(name)}
    </div>
  );
}
