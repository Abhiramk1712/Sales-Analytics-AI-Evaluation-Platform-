/**
 * CompanyHealthPage.jsx — "Company Health": the Executive role's home.
 *
 * "Signal over noise": a single hero verdict, three synthesized callouts
 * (top risk, top mover, forecast confidence), then everything else
 * de-emphasized as "also tracking". Dashboard/ARR Health/Pipeline Health
 * already show the full per-domain detail -- this page's job is to point
 * at what's worth a conversation, not repeat them.
 *
 * The hero verdict is NOT sourced from GET /grading/enterprise-readiness
 * ("Enterprise Grade" tab): that endpoint grades this CODEBASE's own
 * engineering maturity via Path.exists()/hasattr() checks against the repo
 * (backend/grading/enterprise_grader.py) -- it takes no db/company_id and
 * returns the identical score for every tenant. Using it to back a
 * business-health verdict would misrepresent a static engineering
 * self-assessment as company performance. Instead the verdict here is
 * computed from three real, disclosed checks -- NRR >= 100%, no region
 * below the pipeline-coverage floor, and reps-pacing-below-70% share <=
 * 25% -- and names whichever check(s) failed rather than asserting a
 * black-box status.
 *
 * "Top risk" / "top mover" are computed client-side from two quarters of
 * /analytics/reps/performance grouped by `region` (a real, small,
 * top-level field on that response) -- no endpoint aggregates by region
 * with a time dimension. A region is only eligible as a coverage risk if
 * it hasn't yet reached quota this quarter (revenue < quota): a region
 * already over quota with zero remaining pipeline isn't at risk, it's
 * done -- confirmed live against techo-solutions' own data, where APAC's
 * real 0x coverage this quarter is a rep already at 140% of quota, not a
 * risk. Coverage itself is open_pipeline/quota using this same
 * period-scoped quota (already narrowed to one quarter by the period
 * param), the ratio backend/statistics/pipeline_health.py defines --
 * that module's own `/4` is for turning an annual quota into a quarterly
 * one, redundant here since the quota is already quarterly.
 *
 * Forecast confidence comes from a real rolling-origin backtest,
 * GET /ml/forecast/accuracy with no rep_id (company-wide revenue
 * history). Reps-at-risk uses paced_attainment_pct from
 * /ml/forecast/rep-attainment, the same choice made on the Team Command
 * Center page and for the same reason: that endpoint's own
 * motivation_label/base_pct/risk_flag are downstream of a heuristic
 * fallback (backend/ml/attainment_forecaster.py:130) that clamps to a
 * flat 500% for reps with large early-stage pipelines regardless of
 * actual attainment -- confirmed still live and unconditional (the
 * router never passes historical_records, so the real model never fits).
 * paced_attainment_pct is a plain pace extrapolation with no such
 * multiplier.
 */
import { useMemo } from "react";
import { Card, MetricCard, StatusBadge, Skeleton, ErrorMessage } from "../components/shared";
import { useFetch } from "../hooks/useFetch";
import { fmt, pct, withRefresh, withPeriod, toPayoutPeriod } from "../utils/format";

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

// backend/statistics/pipeline_health.py's own bands (>=3 strong, >=2
// healthy, >=1 watch, else risk) computed against a quarterly quota --
// same grain used here.
const COVERAGE_WATCH_FLOOR = 2;

function regionAgg(rows) {
  const map = {};
  for (const r of rows || []) {
    const region = r.region || "Unassigned";
    if (!map[region]) map[region] = { region, revenue: 0, quota: 0, open_pipeline: 0 };
    map[region].revenue += Number(r.revenue || 0);
    map[region].quota += Number(r.quota || 0);
    map[region].open_pipeline += Number(r.open_pipeline || 0);
  }
  return Object.values(map);
}

function accuracyLabel(mape) {
  if (mape == null) return { label: "Unknown", color: "var(--color-text-tertiary)" };
  if (mape < 8) return { label: "High", color: "var(--color-green)" };
  if (mape < 15) return { label: "Moderate", color: "var(--color-amber)" };
  return { label: "Low", color: "var(--color-red)" };
}

export default function CompanyHealthPage({ refreshKey, period, userRole, activeCompany }) {
  const role = userRole || "executive";
  const company = activeCompany || "";

  const { data: kpis, loading: kpisLoading, error: kpisError } = useFetch(
    withPeriod(withRefresh("/analytics/kpis", refreshKey), period),
    { role, company }
  );
  const { data: revops, loading: revopsLoading } = useFetch(
    withPeriod(withRefresh("/analytics/revops-kpis", refreshKey), period),
    { role, company }
  );
  const { data: accuracy, loading: accuracyLoading } = useFetch(
    withRefresh("/ml/forecast/accuracy", refreshKey),
    { role, company }
  );
  const { data: attainData, loading: attainLoading } = useFetch(
    withRefresh("/ml/forecast/rep-attainment", refreshKey),
    { role, company }
  );

  const resolvedPeriod = toPayoutPeriod(period);
  const currentQuarter = /^\d{4}-Q[1-4]$/.test(resolvedPeriod || "") ? resolvedPeriod : toPayoutPeriod("this quarter");
  const priorQuarter = previousQuarterKey(currentQuarter);

  const { data: currPerf, loading: currLoading } = useFetch(
    withPeriod(withRefresh("/analytics/reps/performance", refreshKey), currentQuarter),
    { role, company }
  );
  const { data: priorPerf, loading: priorLoading } = useFetch(
    withPeriod(withRefresh("/analytics/reps/performance", refreshKey), priorQuarter),
    { role, company }
  );

  const currRegions = useMemo(() => regionAgg(currPerf), [currPerf]);
  const priorByRegion = useMemo(() => {
    const map = {};
    for (const r of regionAgg(priorPerf)) map[r.region] = r;
    return map;
  }, [priorPerf]);

  const topRisk = useMemo(() => {
    const candidates = currRegions
      .filter((r) => r.quota > 0 && r.revenue < r.quota)
      .map((r) => ({ ...r, coverage: r.open_pipeline / r.quota }))
      .filter((r) => r.coverage < COVERAGE_WATCH_FLOOR);
    if (!candidates.length) return null;
    return candidates.reduce((worst, r) => (r.coverage < worst.coverage ? r : worst));
  }, [currRegions]);

  const topMover = useMemo(() => {
    const withGrowth = currRegions
      .map((cur) => {
        const prior = priorByRegion[cur.region];
        const priorRevenue = prior ? prior.revenue : 0;
        const growthPct = priorRevenue > 0 ? ((cur.revenue - priorRevenue) / priorRevenue) * 100 : null;
        return { region: cur.region, revenue: cur.revenue, priorRevenue, growthPct };
      })
      .filter((m) => m.growthPct !== null && m.growthPct > 0);
    if (!withGrowth.length) return null;
    return withGrowth.reduce((best, m) => (m.growthPct > best.growthPct ? m : best));
  }, [currRegions, priorByRegion]);

  const repsAtRisk = useMemo(() => {
    const reps = attainData?.reps || [];
    const atRisk = reps.filter((r) => Number(r.paced_attainment_pct ?? 100) < 70);
    return { count: atRisk.length, total: reps.length };
  }, [attainData]);

  const nrrPct = Number(revops?.nrr_pct ?? 0);
  const nrrOk = revops ? nrrPct >= 100 : true;
  const coverageOk = !topRisk;
  const riskSharePct = repsAtRisk.total > 0 ? (repsAtRisk.count / repsAtRisk.total) * 100 : 0;
  const riskShareOk = repsAtRisk.total === 0 || riskSharePct <= 25;
  const onTrack = nrrOk && coverageOk && riskShareOk;

  const verdictReasons = [];
  if (!nrrOk) verdictReasons.push(`NRR is ${pct(nrrPct)}, below 100%`);
  if (!coverageOk) verdictReasons.push(`${topRisk.region} pipeline coverage is ${topRisk.coverage.toFixed(1)}×`);
  if (!riskShareOk) verdictReasons.push(`${repsAtRisk.count} of ${repsAtRisk.total} reps are pacing below 70% of quota`);
  const verdictText = onTrack
    ? "NRR, pipeline coverage, and rep pacing are all healthy this quarter."
    : verdictReasons.join(". ") + ".";

  const mape = accuracy?.backtest?.status === "ok" ? Number(accuracy.backtest.mape) : null;
  const accuracyInfo = accuracyLabel(mape);

  const loading = kpisLoading || revopsLoading || currLoading || priorLoading || attainLoading || accuracyLoading;

  if (kpisError) return <ErrorMessage message={kpisError} />;

  return (
    <div style={{ display: "grid", gap: 16 }}>
      {loading ? (
        <Skeleton h={400} />
      ) : (
        <>
          <Card
            style={{
              padding: "28px 32px",
              display: "grid",
              gridTemplateColumns: "auto 1fr auto",
              alignItems: "center",
              gap: 32,
              background: onTrack
                ? "linear-gradient(120deg, var(--color-background-primary) 55%, var(--color-green-light))"
                : "linear-gradient(120deg, var(--color-background-primary) 55%, var(--color-amber-light))",
            }}
          >
            <div>
              <div style={{ fontSize: 11, fontWeight: 700, color: "var(--color-text-secondary)", textTransform: "uppercase", letterSpacing: "0.7px", marginBottom: 8 }}>
                Company health — {period}
              </div>
              <div style={{ fontSize: 34, fontWeight: 800, letterSpacing: "-1px", color: onTrack ? "var(--color-green)" : "var(--color-amber)" }}>
                {onTrack ? "On Track" : "Needs Attention"}
              </div>
              <div style={{ fontSize: 12.5, color: "var(--color-text-secondary)", marginTop: 8, maxWidth: 420, lineHeight: 1.5 }}>
                {verdictText}
              </div>
            </div>
            <div />
            <div style={{ textAlign: "right", borderLeft: "1px solid var(--color-border-secondary)", paddingLeft: 32 }}>
              <div style={{ fontSize: 30, fontWeight: 800, letterSpacing: "-0.8px" }}>{fmt(revops?.arr_current_12m || 0)}</div>
              <div style={{ fontSize: 12, color: "var(--color-text-secondary)", marginTop: 2 }}>ARR (trailing 12 months)</div>
              {revops && (
                <div
                  style={{
                    display: "inline-flex",
                    alignItems: "center",
                    gap: 4,
                    marginTop: 8,
                    padding: "4px 9px",
                    borderRadius: 999,
                    background: revops.arr_growth_pct >= 0 ? "var(--color-green-light)" : "var(--color-red-light)",
                    color: revops.arr_growth_pct >= 0 ? "var(--color-green)" : "var(--color-red)",
                    fontSize: 11.5,
                    fontWeight: 700,
                  }}
                >
                  {revops.arr_growth_pct >= 0 ? "↑" : "↓"} {pct(Math.abs(revops.arr_growth_pct))} YoY
                </div>
              )}
            </div>
          </Card>

          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))", gap: 16 }}>
            <Card style={{ padding: 22, borderLeft: `3px solid ${topRisk ? "var(--color-red)" : "var(--color-border-secondary)"}` }}>
              <div style={{ fontSize: 11, fontWeight: 700, color: topRisk ? "var(--color-red)" : "var(--color-text-tertiary)", textTransform: "uppercase", letterSpacing: "0.6px", marginBottom: 12 }}>
                Top risk
              </div>
              {topRisk ? (
                <>
                  <div style={{ fontSize: 16, fontWeight: 700, lineHeight: 1.4, marginBottom: 8 }}>
                    {topRisk.region} pipeline coverage fell to {topRisk.coverage.toFixed(1)}×
                  </div>
                  <div style={{ fontSize: 12, color: "var(--color-text-secondary)", lineHeight: 1.55 }}>
                    Below the {COVERAGE_WATCH_FLOOR}× floor this quarter — {fmt(Math.max(0, COVERAGE_WATCH_FLOOR * topRisk.quota - topRisk.open_pipeline))} more open pipeline would reach the {COVERAGE_WATCH_FLOOR}× benchmark.
                  </div>
                </>
              ) : (
                <div style={{ fontSize: 13, color: "var(--color-text-secondary)", lineHeight: 1.55 }}>
                  No region is below the {COVERAGE_WATCH_FLOOR}× coverage floor this quarter.
                </div>
              )}
            </Card>

            <Card style={{ padding: 22, borderLeft: `3px solid ${topMover ? "var(--color-green)" : "var(--color-border-secondary)"}` }}>
              <div style={{ fontSize: 11, fontWeight: 700, color: topMover ? "var(--color-green)" : "var(--color-text-tertiary)", textTransform: "uppercase", letterSpacing: "0.6px", marginBottom: 12 }}>
                Top mover
              </div>
              {topMover ? (
                <>
                  <div style={{ fontSize: 16, fontWeight: 700, lineHeight: 1.4, marginBottom: 8 }}>
                    {topMover.region} revenue up {pct(topMover.growthPct)} quarter over quarter
                  </div>
                  <div style={{ fontSize: 12, color: "var(--color-text-secondary)", lineHeight: 1.55 }}>
                    {fmt(topMover.revenue)} this quarter vs. {fmt(topMover.priorRevenue)} last quarter.
                  </div>
                </>
              ) : (
                <div style={{ fontSize: 13, color: "var(--color-text-secondary)", lineHeight: 1.55 }}>
                  No region grew quarter over quarter.
                </div>
              )}
            </Card>

            <Card style={{ padding: 22, borderLeft: "3px solid var(--color-blue)" }}>
              <div style={{ fontSize: 11, fontWeight: 700, color: "var(--color-blue)", textTransform: "uppercase", letterSpacing: "0.6px", marginBottom: 12 }}>
                Forecast confidence
              </div>
              {mape != null ? (
                <>
                  <div style={{ fontSize: 16, fontWeight: 700, lineHeight: 1.4, marginBottom: 8, color: accuracyInfo.color }}>
                    {accuracyInfo.label} — backtest MAPE {mape.toFixed(1)}%
                  </div>
                  <div style={{ height: 6, borderRadius: 999, background: "var(--color-background-tertiary)", marginTop: 4, marginBottom: 8 }}>
                    <div style={{ width: `${Math.max(0, Math.min(100, 100 - mape))}%`, height: "100%", borderRadius: 999, background: accuracyInfo.color }} />
                  </div>
                  <div style={{ fontSize: 12, color: "var(--color-text-secondary)", lineHeight: 1.55 }}>
                    Rolling-origin backtest, {accuracy.backtest.folds} folds over {accuracy.history_months} months of history.
                  </div>
                </>
              ) : (
                <div style={{ fontSize: 13, color: "var(--color-text-secondary)", lineHeight: 1.55 }}>
                  Not enough revenue history yet to backtest the forecast.
                </div>
              )}
            </Card>
          </div>

          <div style={{ opacity: 0.85 }}>
            <div style={{ fontSize: 10.5, fontWeight: 700, color: "var(--color-text-tertiary)", textTransform: "uppercase", letterSpacing: "0.6px", marginBottom: 8 }}>
              Also tracking — no action needed
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(140px, 1fr))", gap: 10 }}>
              <MiniStat label="Win Rate" value={pct(kpis?.win_rate || 0)} />
              <MiniStat label="Open Pipeline" value={fmt(kpis?.open_pipeline || 0)} />
              <MiniStat label="Quota Attainment" value={pct(kpis?.attainment_pct || 0)} />
              <MiniStat label="NRR" value={revops ? pct(revops.nrr_pct) : "—"} />
              <MiniStat label="Reps At Risk" value={`${repsAtRisk.count} of ${repsAtRisk.total}`} />
            </div>
          </div>
        </>
      )}
    </div>
  );
}

function MiniStat({ label, value }) {
  return (
    <div style={{ background: "var(--color-background-secondary)", borderRadius: 10, padding: "12px 14px" }}>
      <div style={{ fontSize: 10, fontWeight: 600, color: "var(--color-text-tertiary)", textTransform: "uppercase", letterSpacing: "0.5px" }}>{label}</div>
      <div style={{ fontSize: 17, fontWeight: 700, marginTop: 4 }}>{value}</div>
    </div>
  );
}
