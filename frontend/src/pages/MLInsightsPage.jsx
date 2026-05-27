import { useEffect, useMemo, useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { Card, ErrorMessage, MetricCard, SectionTitle, Skeleton } from "../components/shared";
import { useFetch } from "../hooks/useFetch";
import { API, fmt, pct } from "../utils/format";

const SCENARIOS = ["base", "optimistic", "conservative"];

export default function MLInsightsPage({ refreshKey = 0 }) {
  const [target, setTarget] = useState("revenue");
  const [horizon, setHorizon] = useState(6);
  const [scenario, setScenario] = useState("base");
  const [includeLstm, setIncludeLstm] = useState(false);
  const [runData, setRunData] = useState(null);
  const [runLoading, setRunLoading] = useState(true);
  const [runError, setRunError] = useState("");

  const { data: targetsData, loading: targetsLoading, error: targetsError } = useFetch(
    `/ml/forecast/targets?_r=${refreshKey}`
  );
  const { data: explainData, loading: explainLoading, error: explainError } = useFetch(
    `/ml/explain/global-importance?top_n=10&_r=${refreshKey}`
  );
  const { data: evalData, loading: evalLoading, error: evalError } = useFetch(
    `/ml/evaluate/deal-scoring?_r=${refreshKey}`
  );
  const { data: clusteringData, loading: clusteringLoading } = useFetch(`/ml/cluster/reps?_r=${refreshKey}`);
  const includeLstmCandidate = Boolean(includeLstm);
  const isPercentTarget = target === "quota_attainment";

  const formatForecastValue = (value) => (
    isPercentTarget ? `${Number(value || 0).toFixed(1)}%` : fmt(Number(value || 0))
  );

  useEffect(() => {
    const controller = new AbortController();
    const timer = setTimeout(() => {
      setRunLoading(true);
      setRunError("");

      fetch(`${API}/ml/forecast/run`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        signal: controller.signal,
        body: JSON.stringify({
          target,
          horizon,
          scenario,
          include_lstm: includeLstmCandidate,
        }),
      })
        .then(async (res) => {
          const body = await res.json().catch(() => ({}));
          if (!res.ok) {
            throw new Error(body?.detail || `Request failed (${res.status})`);
          }
          return body;
        })
        .then((body) => {
          if (controller.signal.aborted) return;
          setRunData(body);
          setRunLoading(false);
        })
        .catch((err) => {
          if (controller.signal.aborted) return;
          setRunError(err.message || "Failed to run forecast.");
          setRunLoading(false);
        });
    }, 250);

    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [target, horizon, scenario, refreshKey, includeLstmCandidate]);

  const compareData = runData;
  const compareLoading = runLoading;
  const compareError = runError;

  const targetOptions = targetsData?.targets || [];

  const forecastChartData = useMemo(() => {
    if (!runData) return [];
    const periods = runData?.periods || [];
    const values = runData?.values || [];
    const lower = runData?.lower_bound || [];
    const upper = runData?.upper_bound || [];

    return periods.map((period, i) => ({
      period,
      forecast: Number(values[i] || 0),
      lower: Number(lower[i] || 0),
      upper: Number(upper[i] || 0),
    }));
  }, [runData]);

  const leaderboardRows = compareData?.leaderboard || [];
  const topFeatureRows = explainData?.top_features || [];
  const personaRows = useMemo(
    () =>
      (clusteringData?.clusters || []).map((row) => ({
        ...row,
        rep_name: row.rep_name || row.name || `Rep ${row.rep_id}`,
        attainment_pct: Number(row.attainment_pct ?? row.features?.attainment_pct ?? 0),
      })),
    [clusteringData]
  );

  const selectedLeaderboardRow = leaderboardRows.find((row) => row.model === compareData?.selected_model) || leaderboardRows[0] || null;
  const selectedBacktest = selectedLeaderboardRow?.backtest || null;
  const mape = Number(selectedBacktest?.mape || 0);
  const directionalAccuracy = Number(selectedBacktest?.directional_accuracy || 0);

  const qualityVerdict = mape <= 5 && directionalAccuracy >= 70
    ? "High confidence"
    : mape <= 12 && directionalAccuracy >= 55
      ? "Moderate confidence"
      : "Low confidence";

  const confidenceMessage = qualityVerdict === "High confidence"
    ? "Backtest error is tight and directionality is reliable for planning."
    : qualityVerdict === "Moderate confidence"
      ? "Use for directional planning and pair with weekly monitoring."
      : "Use with caution; combine with scenario stress-tests before committing targets.";

  const combinedWarnings = useMemo(() => {
    const merged = [
      ...(runData?.warnings || []),
      ...(selectedBacktest?.warnings || []),
    ].filter(Boolean);
    const deduped = [...new Set(merged)];
    if (!includeLstmCandidate) {
      return deduped.filter((w) => !/lstm|torch/i.test(String(w)));
    }
    return deduped;
  }, [runData, selectedBacktest, includeLstmCandidate]);

  const forecastSummary = useMemo(() => {
    if (!forecastChartData.length) return null;
    const first = forecastChartData[0]?.forecast || 0;
    const last = forecastChartData[forecastChartData.length - 1]?.forecast || 0;
    const change = first === 0 ? 0 : ((last - first) / Math.max(1, first)) * 100;
    return {
      first,
      last,
      change,
      direction: change > 1 ? "up" : change < -1 ? "down" : "flat",
    };
  }, [forecastChartData]);

  const personaSummary = useMemo(() => {
    const counts = {};
    for (const row of personaRows) {
      const key = row.persona || "Unknown";
      counts[key] = (counts[key] || 0) + 1;
    }
    return Object.entries(counts)
      .map(([persona, count]) => ({ persona, count }))
      .sort((a, b) => b.count - a.count);
  }, [personaRows]);

  return (
    <div style={{ display: "grid", gap: 16 }}>
      <Card>
        <SectionTitle>Forecasting Lab Controls</SectionTitle>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(170px, 1fr))", gap: 10 }}>
          <div>
            <div style={{ fontSize: 11, color: "var(--color-text-secondary)", marginBottom: 4 }}>Target</div>
            <select
              value={target}
              onChange={(e) => setTarget(e.target.value)}
              style={{ width: "100%", padding: "6px 8px", borderRadius: 8, border: "0.5px solid var(--color-border-secondary)" }}
            >
              {targetOptions.map((opt) => (
                <option key={opt.target} value={opt.target} disabled={opt.available === false}>
                  {opt.label}{opt.available === false ? " (unavailable)" : ""}
                </option>
              ))}
              {!targetOptions.length && <option value="revenue">Revenue</option>}
            </select>
          </div>
          <div>
            <div style={{ fontSize: 11, color: "var(--color-text-secondary)", marginBottom: 4 }}>Horizon</div>
            <select
              value={horizon}
              onChange={(e) => setHorizon(Number(e.target.value))}
              style={{ width: "100%", padding: "6px 8px", borderRadius: 8, border: "0.5px solid var(--color-border-secondary)" }}
            >
              {[3, 6, 9, 12].map((h) => (
                <option key={h} value={h}>
                  {h} months
                </option>
              ))}
            </select>
          </div>
          <div>
            <div style={{ fontSize: 11, color: "var(--color-text-secondary)", marginBottom: 4 }}>Scenario</div>
            <select
              value={scenario}
              onChange={(e) => setScenario(e.target.value)}
              style={{ width: "100%", padding: "6px 8px", borderRadius: 8, border: "0.5px solid var(--color-border-secondary)" }}
            >
              {SCENARIOS.map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </select>
          </div>
          <div style={{ border: "0.5px solid var(--color-border-tertiary)", borderRadius: 8, padding: 10, background: "var(--color-background-secondary)" }}>
            <div style={{ fontSize: 11, color: "var(--color-text-secondary)", marginBottom: 6 }}>Sequence Candidate</div>
            <label style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 12, color: "var(--color-text-primary)", marginBottom: 5 }}>
              <input
                type="checkbox"
                checked={includeLstmCandidate}
                onChange={(e) => setIncludeLstm(e.target.checked)}
              />
              Include sequence model in comparison
            </label>
            <div style={{ fontSize: 11, color: "var(--color-text-secondary)" }}>
              Disabled by default for faster response. Enable only when testing sequence-model lift.
            </div>
          </div>
          <MetricCard
            label="Selected Model"
            value={compareData?.selected_model || "-"}
            sub={compareData?.selected_strategy || "Awaiting comparison"}
          />
        </div>

        <div style={{ marginTop: 10, display: "grid", gridTemplateColumns: "repeat(3, minmax(0, 1fr))", gap: 10 }}>
          <MetricCard label="MAPE" value={compareLoading ? "..." : `${mape.toFixed(2)}%`} sub="Lower is better" color="#378ADD" />
          <MetricCard label="Directional Accuracy" value={compareLoading ? "..." : `${directionalAccuracy.toFixed(1)}%`} sub="Higher is better" color="#1D9E75" />
          <MetricCard label="Lab Verdict" value={compareLoading ? "..." : qualityVerdict} sub={confidenceMessage} color={qualityVerdict === "High confidence" ? "#1D9E75" : qualityVerdict === "Moderate confidence" ? "#EF9F27" : "#D85A30"} />
        </div>

        {combinedWarnings.length > 0 && (
          <div style={{ marginTop: 10, fontSize: 11, color: "#D85A30" }}>
            Active warnings: {combinedWarnings.slice(0, 3).join(" | ")}
          </div>
        )}

        {targetsLoading && <div style={{ marginTop: 12 }}><Skeleton h={40} /></div>}
        {targetsError && <div style={{ marginTop: 12 }}><ErrorMessage message={targetsError} /></div>}
      </Card>

      <div style={{ display: "grid", gridTemplateColumns: "1.1fr 0.9fr", gap: 16 }}>
        <Card>
          <SectionTitle>Scenario Forecast</SectionTitle>
          {runLoading ? (
            <Skeleton h={260} />
          ) : runError ? (
            <ErrorMessage message={runError} />
          ) : !forecastChartData.length ? (
            <div style={{ fontSize: 12, color: "var(--color-text-secondary)" }}>No forecast output available.</div>
          ) : (
            <ResponsiveContainer width="100%" height={260}>
              <LineChart data={forecastChartData}>
                <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border-tertiary)" />
                <XAxis dataKey="period" tick={{ fontSize: 11 }} />
                <YAxis tick={{ fontSize: 11 }} tickFormatter={(v) => isPercentTarget ? `${Number(v || 0).toFixed(0)}%` : fmt(v)} />
                <Tooltip formatter={(v) => formatForecastValue(v)} />
                <Legend />
                <Line type="monotone" dataKey="forecast" stroke="#1D9E75" strokeWidth={2} dot={false} name="Forecast" />
                <Line type="monotone" dataKey="upper" stroke="#85B7EB" strokeDasharray="4 3" dot={false} name="Upper" />
                <Line type="monotone" dataKey="lower" stroke="#D85A30" strokeDasharray="4 3" dot={false} name="Lower" />
              </LineChart>
            </ResponsiveContainer>
          )}
          {forecastSummary && (
            <div style={{ marginTop: 8, fontSize: 11, color: "var(--color-text-secondary)" }}>
              Forecast trend is {forecastSummary.direction} from {formatForecastValue(forecastSummary.first)} to {formatForecastValue(forecastSummary.last)} ({pct(forecastSummary.change)}). Suggested action: {isPercentTarget
                ? (forecastSummary.direction === "up"
                    ? "lock in stretch accelerators and preserve deal quality controls"
                    : forecastSummary.direction === "down"
                      ? "rebalance quota load, tighten conversion hygiene, and inspect low-attainment segments"
                      : "run quota and territory scenario stress-tests before comp-cycle commitments")
                : (forecastSummary.direction === "up"
                    ? "pre-book implementation and customer success capacity to protect delivery SLAs"
                    : forecastSummary.direction === "down"
                      ? "tighten pipeline creation targets and accelerate late-stage deal inspection"
                      : "run scenario stress tests and focus on margin quality over volume")}.
            </div>
          )}
          {(runData?.warnings || []).length > 0 && (
            <div style={{ marginTop: 10, fontSize: 11, color: "#D85A30" }}>
              Warnings: {(runData.warnings || []).slice(0, 2).join(" | ")}
            </div>
          )}
        </Card>

        <Card>
          <SectionTitle>Model Leaderboard</SectionTitle>
          {compareLoading ? (
            <Skeleton h={260} />
          ) : compareError ? (
            <ErrorMessage message={compareError} />
          ) : (
            <div style={{ maxHeight: 260, overflow: "auto" }}>
              <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
                <thead>
                  <tr>
                    <th style={{ textAlign: "left", padding: "6px 4px" }}>Model</th>
                    <th style={{ textAlign: "right", padding: "6px 4px" }}>MAPE</th>
                    <th style={{ textAlign: "right", padding: "6px 4px" }}>RMSE</th>
                    <th style={{ textAlign: "right", padding: "6px 4px" }}>Rank</th>
                  </tr>
                </thead>
                <tbody>
                  {leaderboardRows.map((row) => (
                    <tr key={row.model} style={{ borderTop: "0.5px solid var(--color-border-tertiary)" }}>
                      <td style={{ padding: "8px 4px" }}>{row.model}</td>
                      <td style={{ textAlign: "right", padding: "8px 4px" }}>{Number(row?.backtest?.mape || 0).toFixed(2)}%</td>
                      <td style={{ textAlign: "right", padding: "8px 4px" }}>{isPercentTarget ? Number(row?.backtest?.rmse || 0).toFixed(2) : fmt(Number(row?.backtest?.rmse || 0))}</td>
                      <td style={{ textAlign: "right", padding: "8px 4px" }}>#{row.rank}</td>
                    </tr>
                  ))}
                  {!leaderboardRows.length && (
                    <tr>
                      <td colSpan={4} style={{ padding: "8px 4px", color: "var(--color-text-secondary)" }}>
                        No comparison results.
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          )}
          {!compareLoading && !compareError && selectedLeaderboardRow && (
            <div style={{ marginTop: 8, fontSize: 11, color: "var(--color-text-secondary)" }}>
              Best model: {selectedLeaderboardRow.model} with MAPE {Number(selectedLeaderboardRow?.backtest?.mape || 0).toFixed(2)}% and directional accuracy {Number(selectedLeaderboardRow?.backtest?.directional_accuracy || 0).toFixed(1)}%.
            </div>
          )}
        </Card>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(3, minmax(0, 1fr))", gap: 12 }}>
        <MetricCard
          label="Deal Model AUC"
          value={evalLoading ? "..." : evalData?.metrics?.roc_auc?.toFixed(3) || "-"}
          sub="Holdout ROC-AUC"
          color="#378ADD"
        />
        <MetricCard
          label="Deal F1"
          value={evalLoading ? "..." : evalData?.metrics?.f1?.toFixed(3) || "-"}
          sub="Binary classification F1"
          color="#1D9E75"
        />
        <MetricCard
          label="Sequence Mode"
          value={includeLstmCandidate ? "Enabled" : "Disabled"}
          sub={includeLstmCandidate ? "Sequence candidate included; run time may increase." : "Sequence candidate excluded for faster turnaround."}
          color={includeLstmCandidate ? "#1D9E75" : "#EF9F27"}
        />
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16 }}>
        <Card>
          <SectionTitle>Global Feature Importance</SectionTitle>
          {explainLoading ? (
            <Skeleton h={220} />
          ) : explainError ? (
            <ErrorMessage message={explainError} />
          ) : !topFeatureRows.length ? (
            <div style={{ fontSize: 12, color: "var(--color-text-secondary)" }}>No feature importance available.</div>
          ) : (
            <ResponsiveContainer width="100%" height={220}>
              <BarChart data={topFeatureRows.slice(0, 8)} layout="vertical" margin={{ left: 10, right: 10, top: 5, bottom: 5 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border-tertiary)" />
                <XAxis type="number" tick={{ fontSize: 11 }} />
                <YAxis dataKey="feature" type="category" width={130} tick={{ fontSize: 11 }} />
                <Tooltip />
                <Bar dataKey="importance" fill="#378ADD" />
              </BarChart>
            </ResponsiveContainer>
          )}
        </Card>

        <Card>
          <SectionTitle>Rep Persona Distribution</SectionTitle>
          {clusteringLoading ? (
            <Skeleton h={220} />
          ) : !personaRows.length ? (
            <div style={{ fontSize: 12, color: "var(--color-text-secondary)" }}>
              Rep clustering data unavailable.
            </div>
          ) : (
            <div style={{ maxHeight: 220, overflow: "auto" }}>
              <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
                <thead>
                  <tr>
                    <th style={{ textAlign: "left", padding: "6px 4px" }}>Rep</th>
                    <th style={{ textAlign: "left", padding: "6px 4px" }}>Persona</th>
                    <th style={{ textAlign: "right", padding: "6px 4px" }}>Attainment</th>
                  </tr>
                </thead>
                <tbody>
                  {personaRows.slice(0, 12).map((row) => (
                    <tr key={row.rep_id} style={{ borderTop: "0.5px solid var(--color-border-tertiary)" }}>
                      <td style={{ padding: "8px 4px" }}>{row.rep_name}</td>
                      <td style={{ padding: "8px 4px" }}>{row.persona}</td>
                      <td style={{ textAlign: "right", padding: "8px 4px" }}>{pct(row.attainment_pct || 0)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {!clusteringLoading && personaSummary.length > 0 && (
            <div style={{ marginTop: 8, fontSize: 11, color: "var(--color-text-secondary)" }}>
              Dominant persona: {personaSummary[0].persona} ({personaSummary[0].count} reps). Use this to tune coaching programs and plan design by segment.
            </div>
          )}
        </Card>
      </div>

      {evalError && <ErrorMessage message={evalError} />}
    </div>
  );
}
