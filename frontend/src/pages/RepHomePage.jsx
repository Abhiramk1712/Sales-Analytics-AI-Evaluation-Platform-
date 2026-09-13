/**
 * RepHomePage.jsx — "My Performance": the Sales Rep role's home.
 *
 * Replaces the generic Dashboard for sales_rep with a personal, motivational
 * view: a tier-banded quota ring (drawn from the rep's own assigned plan
 * rules, not a hardcoded 100/130% split), a streak of consecutive quarters
 * at or above quota, peer percentile, a transparent payout estimate, a few
 * earned milestones, and a short "do this next" list instead of a raw deal
 * table.
 *
 * There is no per-user login in this demo (role is asserted, not a specific
 * identity) — same as Rep Scorecard, this page lets you pick which rep's
 * home to view, defaulting to the top performer.
 *
 * No endpoint here has a side effect. The payout estimate is computed
 * client-side from the rep's own revenue and their current tier's rate/bonus
 * (both from GET /analytics/reps/{id}/profile) rather than calling
 * POST /payout/calculate, which writes an audit-trail record as a side
 * effect of "calculating" — not appropriate to trigger just from loading a
 * read-only summary page.
 */
import { useEffect, useMemo, useState } from "react";
import { Card, MetricCard, SectionTitle, Skeleton, ErrorMessage } from "../components/shared";
import { useFetch } from "../hooks/useFetch";
import { fmt, pct, withRefresh, withPeriod } from "../utils/format";

const TIER_COLORS = ["var(--color-blue)", "var(--color-amber)", "var(--color-green)", "#8b5cf6", "#ec4899"];

function tierLabel(rule) {
  if (!rule) return "";
  const min = rule.threshold_min ?? 0;
  const range = rule.threshold_max != null && rule.threshold_max < 999 ? `${min}–${rule.threshold_max}%` : `${min}%+`;
  return `${rule.name || "Tier"} · ${range}`;
}

/** Fractional [0,1] track/fill segments for an N-tier ring, scaled so the
 * current attainment and the highest real threshold are both visible. */
function buildRingSegments(rules, attainmentPct) {
  if (!rules.length) {
    const scaleMax = Math.max(attainmentPct * 1.15, 100);
    const frac = Math.max(0, Math.min(1, attainmentPct / scaleMax));
    return {
      scaleMax,
      tracks: [{ start: 0, end: 1, color: "var(--color-border-secondary)" }],
      fills: frac > 0 ? [{ start: 0, end: frac, color: "var(--color-accent-primary)" }] : [],
    };
  }
  const lastRule = rules[rules.length - 1];
  const lastMax = lastRule.threshold_max != null && lastRule.threshold_max < 999 ? lastRule.threshold_max : null;
  const scaleMax = Math.max(
    attainmentPct * 1.1,
    lastMax != null ? lastMax * 1.15 : (lastRule.threshold_min ?? 100) * 1.5,
    100
  );
  const tracks = [];
  const fills = [];
  rules.forEach((r, i) => {
    const start = Math.max(0, (r.threshold_min ?? 0) / scaleMax);
    const rawEnd = (r.threshold_max != null && r.threshold_max < 999 ? r.threshold_max : scaleMax) / scaleMax;
    const end = Math.min(1, rawEnd);
    const color = TIER_COLORS[i % TIER_COLORS.length];
    tracks.push({ start, end, color });
    const fillEnd = Math.min(end, attainmentPct / scaleMax);
    if (fillEnd > start) fills.push({ start, end: fillEnd, color });
  });
  return { scaleMax, tracks, fills };
}

function TierRing({ tracks, fills, size = 240, strokeWidth = 16 }) {
  const r = (size - strokeWidth) / 2;
  const c = 2 * Math.PI * r;
  const center = size / 2;
  return (
    <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`}>
      {tracks.map((t, i) => (
        <circle
          key={`track-${i}`} cx={center} cy={center} r={r} fill="none" stroke={t.color} strokeOpacity={0.16}
          strokeWidth={strokeWidth} transform={`rotate(-90 ${center} ${center})`}
          strokeDasharray={`${Math.max(0, (t.end - t.start) * c)} ${c}`} strokeDashoffset={-t.start * c}
        />
      ))}
      {fills.map((f, i) => (
        <circle
          key={`fill-${i}`} cx={center} cy={center} r={r} fill="none" stroke={f.color}
          strokeWidth={strokeWidth} strokeLinecap="round" transform={`rotate(-90 ${center} ${center})`}
          strokeDasharray={`${Math.max(0, (f.end - f.start) * c)} ${c}`} strokeDashoffset={-f.start * c}
        />
      ))}
    </svg>
  );
}

export default function RepHomePage({ refreshKey, activeCompany, userRole, period }) {
  const role = userRole || "sales_rep";
  const company = activeCompany || "";
  const [selectedRepId, setSelectedRepId] = useState(null);

  const { data: repsData, loading: repsLoading, error: repsError } = useFetch(
    withPeriod(withRefresh("/analytics/reps/performance", refreshKey), period), { role, company }
  );
  const sortedReps = useMemo(
    () => [...(repsData || [])].sort((a, b) => Number(b.attainment_pct || 0) - Number(a.attainment_pct || 0)),
    [repsData]
  );

  // Company switch invalidates the selection (stale UUID from the old
  // dataset) — same pattern as Rep Scorecard.
  useEffect(() => { setSelectedRepId(null); }, [refreshKey]);
  useEffect(() => {
    if (selectedRepId || !sortedReps.length) return;
    setSelectedRepId(sortedReps[0].rep_id);
  }, [selectedRepId, sortedReps]);

  const { data: profile, loading: profileLoading, error: profileError } = useFetch(
    selectedRepId ? withPeriod(`/analytics/reps/${selectedRepId}/profile`, period) : null, { role, company }
  );
  const { data: stmtData } = useFetch(
    selectedRepId ? `/payout/statements/${selectedRepId}?periods=24` : null, { role, company }
  );
  const { data: attainData } = useFetch(withRefresh("/ml/forecast/rep-attainment", refreshKey), { role, company });
  const { data: winsData } = useFetch(
    selectedRepId ? `/analytics/reps/${selectedRepId}/deals?stage=${encodeURIComponent("Closed Won")}&limit=20` : null,
    { role, company }
  );

  const perf = profile?.performance || {};
  const attainmentPct = Number(perf.attainment_pct || 0);

  const rules = useMemo(
    () => [...(profile?.assigned_rules || [])].sort((a, b) => (a.threshold_min ?? 0) - (b.threshold_min ?? 0)),
    [profile]
  );

  const currentTierIdx = useMemo(() => {
    if (!rules.length) return -1;
    const idx = rules.findIndex(
      (r) => attainmentPct >= (r.threshold_min ?? 0) && attainmentPct < (r.threshold_max ?? Infinity)
    );
    if (idx !== -1) return idx;
    return attainmentPct >= (rules[0].threshold_min ?? 0) ? rules.length - 1 : 0;
  }, [rules, attainmentPct]);
  const currentTier = currentTierIdx >= 0 ? rules[currentTierIdx] : null;
  const nextTier = currentTierIdx >= 0 && currentTierIdx < rules.length - 1 ? rules[currentTierIdx + 1] : null;
  const ring = useMemo(() => buildRingSegments(rules, attainmentPct), [rules, attainmentPct]);

  // Streak: /payout/statements returns monthly rows, each tagged with the
  // quarter it belongs to (see backend/routers/payout.py) — aggregate back
  // up to quarterly revenue/quota, since that's the plan's real evaluation
  // grain, then count consecutive quarters at or above 100% from the most
  // recent backward.
  const { streak, quarterHistory } = useMemo(() => {
    const statements = stmtData?.statements || [];
    const byQuarter = {};
    for (const s of statements) {
      const q = s.quarter || s.period;
      if (!byQuarter[q]) byQuarter[q] = { revenue: 0, quota: 0 };
      byQuarter[q].revenue += Number(s.revenue || 0);
      byQuarter[q].quota += Number(s.quota || 0);
    }
    const quarters = Object.keys(byQuarter).sort();
    const history = quarters.map((q) => ({
      quarter: q,
      attainment_pct: byQuarter[q].quota > 0 ? (byQuarter[q].revenue / byQuarter[q].quota) * 100 : 0,
    }));
    let s = 0;
    for (let i = history.length - 1; i >= 0; i--) {
      if (history[i].attainment_pct >= 100) s++;
      else break;
    }
    return { streak: s, quarterHistory: history.slice(-6) };
  }, [stmtData]);

  const rank = profile?.rank;
  const totalReps = profile?.total_reps;
  // "Ahead of X% of reps" reads correctly in both directions -- a "Top 73%"
  // framing (rank / total) sounds like a good result even for a below-
  // average rank (confirmed: rank #8 of 11 rendered as "Top 73%", which
  // reads as strong when it's actually bottom-third).
  const aheadOfPct = rank && totalReps > 1 ? Math.max(0, Math.round(((totalReps - rank) / (totalReps - 1)) * 100)) : null;

  const repForecastRow = useMemo(
    () => (attainData?.reps || []).find((r) => r.rep_id === selectedRepId),
    [attainData, selectedRepId]
  );
  const focusDeals = (repForecastRow?.top_focus_deals || []).slice(0, 2);

  const recentWins = useMemo(() => {
    const deals = winsData?.deals || [];
    return [...deals]
      .sort((a, b) =>
        String(b.actual_close_date || b.created_at || "").localeCompare(String(a.actual_close_date || a.created_at || ""))
      )
      .slice(0, 3);
  }, [winsData]);

  // Transparent, side-effect-free estimate: this quarter's revenue at the
  // current tier's own rate/bonus — not a call to POST /payout/calculate,
  // which persists an audit-trail record as a side effect of "calculating"
  // and has no business running just because a rep opened this page.
  const projectedPayout = currentTier
    ? Number(perf.revenue || 0) * Number(currentTier.rate || 0) + Number(currentTier.bonus_amount || 0)
    : null;
  const revenueToNextTier = nextTier ? Math.max(0, ((nextTier.threshold_min ?? 0) / 100) * Number(perf.quota || 0) - Number(perf.revenue || 0)) : 0;

  const badges = useMemo(() => {
    const list = [];
    if (attainmentPct >= 100) list.push({ key: "crusher", label: "Quota Crusher", detail: `${pct(attainmentPct)} this period` });
    if (streak >= 3) list.push({ key: "fire", label: "On Fire", detail: `${streak}-quarter streak` });
    if (rank === 1 && totalReps > 1) list.push({ key: "top", label: "Top Performer", detail: `#1 of ${totalReps}` });
    if (Number(perf.win_rate || 0) >= 70) list.push({ key: "winrate", label: "Win Rate Leader", detail: `${pct(perf.win_rate)} win rate` });
    if (rules.length > 1 && currentTierIdx === rules.length - 1) {
      list.push({ key: "tier", label: `${currentTier?.name || "Top Tier"}`, detail: "Highest rate unlocked" });
    }
    return list.slice(0, 4);
  }, [attainmentPct, streak, rank, totalReps, perf, rules, currentTierIdx, currentTier]);

  const loading = repsLoading || (!!selectedRepId && profileLoading);

  if (repsError) return <ErrorMessage message={repsError} />;
  if (!repsLoading && !sortedReps.length) {
    return <div style={{ fontSize: 13, color: "var(--color-text-secondary)", padding: 20 }}>No reps found for this company.</div>;
  }

  return (
    <div style={{ display: "grid", gap: 16 }}>
      {/* Rep picker + greeting */}
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", flexWrap: "wrap", gap: 10 }}>
        <div>
          <div style={{ fontSize: 11, color: "var(--color-text-secondary)", textTransform: "uppercase", letterSpacing: "0.5px", fontWeight: 600, marginBottom: 4 }}>
            My Performance
          </div>
          <select
            value={selectedRepId || ""}
            onChange={(e) => setSelectedRepId(e.target.value)}
            style={{ padding: "7px 10px", borderRadius: "var(--border-radius-md)", border: "1px solid var(--color-border-secondary)", background: "var(--color-background-primary)", fontSize: 15, fontWeight: 700, color: "var(--color-text-primary)" }}
          >
            {sortedReps.map((r) => <option key={r.rep_id} value={r.rep_id}>{r.name}</option>)}
          </select>
        </div>
        {profile?.position && (
          <div style={{ fontSize: 12, color: "var(--color-text-secondary)" }}>{profile.position}{profile.region ? ` · ${profile.region}` : ""}</div>
        )}
      </div>

      {profileError && <ErrorMessage message={profileError} />}

      {loading ? <Skeleton h={280} /> : (
        <>
          {repForecastRow?.motivation_msg && (
            <div style={{ padding: "10px 14px", borderRadius: "var(--border-radius-md)", background: "var(--color-blue-light)", color: "var(--color-text-primary)", fontSize: 12.5, fontWeight: 500 }}>
              {repForecastRow.motivation_msg}
            </div>
          )}

          {/* Hero: ring + side stats */}
          <div style={{ display: "grid", gridTemplateColumns: "1.15fr 0.85fr", gap: 16 }}>
            <Card style={{ display: "flex", alignItems: "center", gap: 28, padding: 24 }}>
              <div style={{ position: "relative", width: 240, height: 240, flexShrink: 0 }}>
                <TierRing tracks={ring.tracks} fills={ring.fills} />
                <div style={{ position: "absolute", inset: 0, display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center" }}>
                  <div style={{ fontSize: 40, fontWeight: 800, letterSpacing: "-1.5px", lineHeight: 1 }}>{pct(attainmentPct)}</div>
                  <div style={{ fontSize: 12, color: "var(--color-text-secondary)", fontWeight: 600, marginTop: 4 }}>of quota</div>
                  {currentTier && (
                    <div style={{ marginTop: 10, padding: "3px 10px", borderRadius: 999, background: "var(--color-amber-light)", color: "#92400E", fontSize: 10.5, fontWeight: 700, letterSpacing: "0.3px", textTransform: "uppercase" }}>
                      {currentTier.name || "Current tier"}
                    </div>
                  )}
                </div>
              </div>
              <div style={{ display: "flex", flexDirection: "column", gap: 12, flex: 1, minWidth: 0 }}>
                <div style={{ fontSize: 13, fontWeight: 700 }}>Tier progress</div>
                {rules.length ? rules.map((r, i) => (
                  <div key={r.rule_id || i} style={{ display: "flex", alignItems: "center", gap: 10 }}>
                    <span style={{ width: 10, height: 10, borderRadius: 3, background: TIER_COLORS[i % TIER_COLORS.length], flexShrink: 0 }} />
                    <span style={{ fontSize: 12, fontWeight: i === currentTierIdx ? 700 : 500, flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                      {tierLabel(r)}
                    </span>
                    <span style={{ fontSize: 11, fontWeight: 600, color: i === currentTierIdx ? "#92400E" : "var(--color-text-tertiary)", whiteSpace: "nowrap" }}>
                      {i === currentTierIdx ? "you are here" : i < currentTierIdx ? "complete" : ""}
                    </span>
                  </div>
                )) : (
                  <div style={{ fontSize: 12, color: "var(--color-text-secondary)" }}>No commission plan assigned for this period.</div>
                )}
                {nextTier && revenueToNextTier > 0 && (
                  <div style={{ fontSize: 12, color: "var(--color-text-secondary)", lineHeight: 1.5, marginTop: 4 }}>
                    <strong style={{ color: "var(--color-text-primary)" }}>{fmt(revenueToNextTier)}</strong> of revenue from the {nextTier.name} tier.
                  </div>
                )}
              </div>
            </Card>

            <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
              <MetricCard label="Streak" value={streak > 0 ? `${streak} quarter${streak === 1 ? "" : "s"}` : "—"} sub="At or above quota, consecutively" color={streak >= 3 ? "var(--color-amber)" : undefined} />
              <MetricCard label="Revenue Rank" value={aheadOfPct != null ? `Ahead of ${aheadOfPct}%` : "—"} sub={rank && totalReps ? `#${rank} of ${totalReps} reps, by revenue` : "Not enough peer data"} />
              <Card style={{ padding: "16px 18px" }}>
                <div style={{ fontSize: 11, fontWeight: 700, color: "var(--color-text-secondary)", textTransform: "uppercase", letterSpacing: "0.6px", marginBottom: 6 }}>Est. Commission This Period</div>
                <div style={{ fontSize: 26, fontWeight: 800, color: "var(--color-accent-primary)", letterSpacing: "-0.6px" }}>
                  {projectedPayout != null ? fmt(projectedPayout) : "—"}
                </div>
                <div style={{ fontSize: 11, color: "var(--color-text-tertiary)", marginTop: 4 }}>
                  {currentTier ? `${fmt(perf.revenue)} revenue × ${pct((currentTier.rate || 0) * 100)} rate` : "No plan assigned"}
                </div>
              </Card>
            </div>
          </div>

          {/* Badges */}
          <Card>
            <SectionTitle sub="Earned this period">Milestones</SectionTitle>
            {badges.length ? (
              <div style={{ display: "grid", gridTemplateColumns: `repeat(${badges.length}, 1fr)`, gap: 12 }}>
                {badges.map((b) => (
                  <div key={b.key} style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 6, padding: "14px 10px", borderRadius: "var(--border-radius-md)", background: "var(--color-amber-light)", border: "1px solid #fbd9a5" }}>
                    <div style={{ fontSize: 12, fontWeight: 700, textAlign: "center" }}>{b.label}</div>
                    <div style={{ fontSize: 10.5, color: "var(--color-text-secondary)", textAlign: "center" }}>{b.detail}</div>
                  </div>
                ))}
              </div>
            ) : (
              <div style={{ fontSize: 12, color: "var(--color-text-secondary)" }}>No milestones yet this period — closing a deal or hitting quota earns your first one.</div>
            )}
          </Card>

          {/* Next best action + recent wins */}
          <div style={{ display: "grid", gridTemplateColumns: "1.4fr 1fr", gap: 16 }}>
            <Card>
              <SectionTitle sub={focusDeals.length ? "Ranked by expected value (amount × win probability)" : undefined}>Do this next</SectionTitle>
              {focusDeals.length ? (
                <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
                  {focusDeals.map((d) => (
                    <div key={d.deal_id} style={{ display: "flex", alignItems: "flex-start", gap: 12, padding: 14, borderRadius: "var(--border-radius-md)", background: "var(--color-background-secondary)", border: "1px solid var(--color-border-secondary)" }}>
                      <div style={{ flex: 1, minWidth: 0 }}>
                        <div style={{ display: "flex", justifyContent: "space-between", gap: 8 }}>
                          <div style={{ fontSize: 13, fontWeight: 700, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{d.name}</div>
                          <div style={{ fontSize: 13, fontWeight: 700, whiteSpace: "nowrap" }}>{fmt(d.amount)}</div>
                        </div>
                        <div style={{ fontSize: 11.5, color: "var(--color-text-secondary)", marginTop: 4 }}>
                          {d.close_probability}% win probability · <span style={{ color: "var(--color-green)", fontWeight: 600 }}>{fmt(d.weighted_value)} expected</span> · close by {d.expected_close_date || "—"}
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              ) : (
                <div style={{ fontSize: 12, color: "var(--color-text-secondary)" }}>No open deals flagged this period.</div>
              )}
            </Card>

            <Card>
              <SectionTitle sub="Closed — Won">Recent wins</SectionTitle>
              {recentWins.length ? (
                <div>
                  {recentWins.map((w) => (
                    <div key={w.deal_id} style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "10px 0", borderBottom: "1px solid var(--color-border-tertiary)" }}>
                      <div>
                        <div style={{ fontSize: 12.5, fontWeight: 600 }}>{w.account || w.name}</div>
                        <div style={{ fontSize: 10.5, color: "var(--color-text-tertiary)", marginTop: 2 }}>{w.actual_close_date || "—"}</div>
                      </div>
                      <div style={{ fontSize: 13, fontWeight: 700, color: "var(--color-green)" }}>{fmt(w.amount)}</div>
                    </div>
                  ))}
                </div>
              ) : (
                <div style={{ fontSize: 12, color: "var(--color-text-secondary)" }}>No closed-won deals yet this period.</div>
              )}
            </Card>
          </div>
        </>
      )}
    </div>
  );
}
