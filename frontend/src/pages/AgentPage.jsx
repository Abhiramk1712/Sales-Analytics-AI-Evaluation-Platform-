/**
 * AgentPage.jsx — AI chat with SSE streaming via /agent/chat/stream
 * Sprint 2.4
 */
import { useState, useRef, useCallback } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  Line,
  LineChart,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { API } from "../utils/format";
import { Card } from "../components/shared";

const WELCOME = "Hi! I'm your sales intelligence assistant. Ask me about pipeline health, forecast accuracy, quota attainment, rep performance, or ARR trends.";

function TypingIndicator() {
  return (
    <div style={{ display: "flex", gap: 4, alignItems: "center", padding: "8px 12px" }}>
      {[0, 1, 2].map((i) => (
        <span
          key={i}
          style={{
            width: 6,
            height: 6,
            borderRadius: "50%",
            background: "#378ADD",
            opacity: 0.7,
            animation: `bounce 1.2s ${i * 0.2}s infinite`,
          }}
        />
      ))}
    </div>
  );
}

function formatChartValue(unit, value) {
  const num = Number(value || 0);
  if (unit === "currency") {
    if (Math.abs(num) >= 1_000_000) return `$${(num / 1_000_000).toFixed(2)}M`;
    if (Math.abs(num) >= 1_000) return `$${(num / 1_000).toFixed(1)}K`;
    return `$${num.toFixed(0)}`;
  }
  if (unit === "percent") return `${num.toFixed(1)}%`;
  if (unit === "days") return `${num.toFixed(1)} d`;
  return Number.isFinite(num) ? num.toFixed(2) : String(value);
}

function AgentChart({ chart }) {
  if (!chart || !Array.isArray(chart.data) || chart.data.length === 0) return null;
  const type = chart.type || "bar";
  const series = Array.isArray(chart.series) ? chart.series : [];
  const xKey = chart.xKey || "name";
  const unit = chart.unit || "number";
  const height = Number(chart.height || 220);

  const tooltipFormatter = (value, name) => [formatChartValue(unit, value), name];

  return (
    <div
      style={{
        marginTop: 10,
        border: "0.5px solid var(--color-border-tertiary)",
        borderRadius: 10,
        padding: "10px 10px 6px",
        background: "var(--color-background-primary)",
      }}
    >
      <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 8, color: "var(--color-text-primary)" }}>
        {chart.title || "Chart"}
      </div>
      <div style={{ width: "100%", height }}>
        <ResponsiveContainer width="100%" height="100%">
          {type === "line" ? (
            <LineChart data={chart.data}>
              <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border-tertiary)" />
              <XAxis dataKey={xKey} tick={{ fontSize: 11 }} />
              <YAxis tick={{ fontSize: 11 }} tickFormatter={(v) => formatChartValue(unit, v)} />
              <Tooltip formatter={tooltipFormatter} />
              <Legend wrapperStyle={{ fontSize: 11 }} />
              {series.map((s, idx) => (
                <Line
                  key={`${s.key}-${idx}`}
                  type="monotone"
                  dataKey={s.key}
                  name={s.label || s.key}
                  stroke={s.color || "#378ADD"}
                  strokeWidth={2}
                  dot={false}
                />
              ))}
            </LineChart>
          ) : type === "pie" ? (
            <PieChart>
              <Tooltip formatter={tooltipFormatter} />
              <Legend wrapperStyle={{ fontSize: 11 }} />
              <Pie
                data={chart.data}
                dataKey={series[0]?.key || "value"}
                nameKey={xKey || "name"}
                outerRadius={78}
                innerRadius={32}
                label
              >
                {chart.data.map((row, idx) => (
                  <Cell key={`pie-${idx}`} fill={row.fill || series[idx]?.color || "#378ADD"} />
                ))}
              </Pie>
            </PieChart>
          ) : type === "stacked-bar" ? (
            <BarChart data={chart.data}>
              <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border-tertiary)" />
              <XAxis dataKey={xKey} tick={{ fontSize: 11 }} />
              <YAxis tick={{ fontSize: 11 }} tickFormatter={(v) => formatChartValue(unit, v)} />
              <Tooltip formatter={tooltipFormatter} />
              <Legend wrapperStyle={{ fontSize: 11 }} />
              {series.map((s, idx) => (
                <Bar
                  key={`${s.key}-${idx}`}
                  dataKey={s.key}
                  name={s.label || s.key}
                  fill={s.color || "#378ADD"}
                  stackId={s.stackId || "total"}
                  radius={[4, 4, 0, 0]}
                />
              ))}
            </BarChart>
          ) : (
            <BarChart data={chart.data}>
              <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border-tertiary)" />
              <XAxis dataKey={xKey} tick={{ fontSize: 11 }} />
              <YAxis tick={{ fontSize: 11 }} tickFormatter={(v) => formatChartValue(unit, v)} />
              <Tooltip formatter={tooltipFormatter} />
              <Legend wrapperStyle={{ fontSize: 11 }} />
              {series.map((s, idx) => (
                <Bar
                  key={`${s.key}-${idx}`}
                  dataKey={s.key}
                  name={s.label || s.key}
                  fill={s.color || "#378ADD"}
                  radius={[4, 4, 0, 0]}
                />
              ))}
            </BarChart>
          )}
        </ResponsiveContainer>
      </div>
    </div>
  );
}

function Message({ msg }) {
  const isUser = msg.role === "user";
  return (
    <div
      style={{
        display: "flex",
        justifyContent: isUser ? "flex-end" : "flex-start",
        marginBottom: 12,
      }}
    >
      <div
        style={{
          maxWidth: "80%",
          padding: "10px 14px",
          borderRadius: isUser ? "12px 12px 4px 12px" : "12px 12px 12px 4px",
          background: isUser ? "#378ADD" : "var(--color-background-secondary)",
          color: isUser ? "#fff" : "var(--color-text-primary)",
          fontSize: 13,
          lineHeight: 1.6,
          whiteSpace: "pre-wrap",
        }}
      >
        {msg.content}
        {msg.streaming && <span style={{ opacity: 0.5 }}>▌</span>}
        {!isUser && msg.answerQuality && <QualityBadge quality={msg.answerQuality} />}
        {!isUser && Array.isArray(msg.charts) && msg.charts.length > 0 && (
          <div style={{ display: "grid", gap: 8 }}>
            {msg.charts.map((chart, idx) => (
              <AgentChart key={chart.id || chart.title || `chart-${idx}`} chart={chart} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function ToolBadge({ tool }) {
  return (
    <span
      style={{
        display: "inline-block",
        padding: "2px 8px",
        borderRadius: 12,
        background: "var(--color-background-secondary)",
        fontSize: 10,
        color: "var(--color-text-secondary)",
        border: "0.5px solid var(--color-border-tertiary)",
        marginRight: 4,
      }}
    >
      {tool}
    </span>
  );
}

function qualityColor(level) {
  if (level === "high") return "#2DA44E";
  if (level === "medium") return "#D97706";
  return "#D85A30";
}

function QualityBadge({ quality }) {
  if (!quality || typeof quality !== "object") return null;
  const level = String(quality.level || "medium").toLowerCase();
  const score = Number(quality.score || 0);
  const dims = quality.dimensions || {};
  const coverage = Number(dims.coverage?.score || 0);
  const confidence = Number(dims.confidence?.score || 0);
  const freshness = Number(dims.freshness?.score || 0);
  const color = qualityColor(level);

  return (
    <div
      style={{
        marginTop: 8,
        padding: "8px 10px",
        borderRadius: 8,
        border: `1px solid ${color}33`,
        background: `${color}12`,
        color: "var(--color-text-primary)",
      }}
    >
      <div style={{ fontSize: 11, fontWeight: 700, color }}>
        Trust {level.toUpperCase()} · {score}/100
      </div>
      <div style={{ fontSize: 11, marginTop: 4 }}>
        Coverage {coverage}/100 · Confidence {confidence}/100 · Freshness {freshness}/100
      </div>
      {quality.summary && (
        <div style={{ fontSize: 11, marginTop: 4, color: "var(--color-text-secondary)" }}>{quality.summary}</div>
      )}
    </div>
  );
}

export default function AgentPage({ activeCompany, userRole } = {}) {
  const [messages, setMessages] = useState([
    { id: "welcome", role: "assistant", content: WELCOME },
  ]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [toolsUsed, setToolsUsed] = useState([]);
  const [intent, setIntent] = useState("");
  const [answerQuality, setAnswerQuality] = useState(null);
  const [streamError, setStreamError] = useState(null);
  const abortRef = useRef(null);
  const bottomRef = useRef(null);

  const scrollToBottom = () => {
    setTimeout(() => bottomRef.current?.scrollIntoView({ behavior: "smooth" }), 50);
  };

  const sendMessage = useCallback(async () => {
    if (!input.trim() || loading) return;

    const userMsg = { id: Date.now(), role: "user", content: input.trim() };
    const history = messages
      .filter((m) => m.id !== "welcome")
      .map((m) => ({ role: m.role, content: m.content }));

    setMessages((prev) => [...prev, userMsg]);
    setInput("");
    setLoading(true);
    setStreamError(null);
    setToolsUsed([]);
    setIntent("");
    setAnswerQuality(null);

    // Add placeholder assistant message
    const assistantId = Date.now() + 1;
    setMessages((prev) => [
      ...prev,
      { id: assistantId, role: "assistant", content: "", streaming: true },
    ]);
    scrollToBottom();

    try {
      const controller = new AbortController();
      abortRef.current = controller;

      const res = await fetch(`${API}/agent/chat/stream`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...(userRole ? { "X-User-Role": userRole } : {}),
          ...(activeCompany ? { "X-Company-Id": activeCompany } : {}),
        },
        body: JSON.stringify({ message: userMsg.content, history }),
        signal: controller.signal,
      });

      if (!res.ok) {
        throw new Error(`Stream request failed (${res.status})`);
      }

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let accumulated = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        const chunk = decoder.decode(value, { stream: true });
        const lines = chunk.split("\n");

        for (const line of lines) {
          if (!line.startsWith("data: ")) continue;
          try {
            const payload = JSON.parse(line.slice(6));
            if (payload.done) {
              // Final event
              setMessages((prev) =>
                prev.map((m) =>
                  m.id === assistantId
                    ? {
                        ...m,
                        content: payload.full_response || accumulated,
                        charts: Array.isArray(payload.charts) ? payload.charts : [],
                        answerQuality: payload.answer_quality || null,
                        streaming: false,
                      }
                    : m
                )
              );
              setToolsUsed(payload.tool_calls || []);
              setIntent(payload.intent || "");
              setAnswerQuality(payload.answer_quality || null);
            } else {
              accumulated += payload.delta || "";
              setMessages((prev) =>
                prev.map((m) =>
                  m.id === assistantId
                    ? { ...m, content: accumulated, streaming: true }
                    : m
                )
              );
              scrollToBottom();
            }
          } catch {
            // Non-JSON SSE line, ignore
          }
        }
      }
    } catch (err) {
      if (err.name === "AbortError") {
        setMessages((prev) =>
          prev.map((m) =>
            m.id === assistantId
              ? { ...m, content: "[Cancelled]", streaming: false }
              : m
          )
        );
      } else {
        // Fall back to non-streaming endpoint
        try {
          const fallbackRes = await fetch(`${API}/agent/chat`, {
            method: "POST",
            headers: {
              "Content-Type": "application/json",
              ...(userRole ? { "X-User-Role": userRole } : {}),
              ...(activeCompany ? { "X-Company-Id": activeCompany } : {}),
            },
            body: JSON.stringify({ message: userMsg.content, history }),
          });
          const fallbackData = await fallbackRes.json();
          setMessages((prev) =>
            prev.map((m) =>
              m.id === assistantId
                ? {
                    ...m,
                    content: fallbackData.reply || "No response",
                    charts: Array.isArray(fallbackData.charts) ? fallbackData.charts : [],
                    answerQuality: fallbackData.answer_quality || null,
                    streaming: false,
                  }
                : m
            )
          );
          setToolsUsed(fallbackData.tools_used || []);
          setIntent(fallbackData.intent || "");
          setAnswerQuality(fallbackData.answer_quality || null);
        } catch {
          setStreamError("Unable to reach AI agent. Check that the backend is running.");
          setMessages((prev) =>
            prev.map((m) =>
              m.id === assistantId
                ? { ...m, content: "[Connection error — see error banner]", streaming: false }
                : m
            )
          );
        }
      }
    } finally {
      setLoading(false);
      scrollToBottom();
    }
  }, [input, loading, messages, userRole, activeCompany]);

  const handleKeyDown = (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  };

  const handleStop = () => {
    abortRef.current?.abort();
    setLoading(false);
  };

  const SUGGESTIONS = [
    "What's our current pipeline health?",
    "Which reps are underperforming?",
    "Show me deal velocity trends",
    "Forecast accuracy for Q3?",
    "Which deals are at risk of slipping?",
  ];

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "calc(100vh - 120px)", maxHeight: 700 }}>
      {/* Tool badge bar */}
      {(toolsUsed.length > 0 || intent || answerQuality) && (
        <div style={{ padding: "6px 0 10px", display: "flex", gap: 6, alignItems: "center", flexWrap: "wrap" }}>
          {intent && (
            <span style={{ fontSize: 10, color: "var(--color-text-secondary)", marginRight: 4 }}>
              Intent: <strong>{intent}</strong>
            </span>
          )}
          {answerQuality && (
            <span style={{ fontSize: 10, color: qualityColor(answerQuality.level), marginRight: 6, fontWeight: 700 }}>
              Trust: {String(answerQuality.level || "medium").toUpperCase()} {Number(answerQuality.score || 0)}/100
            </span>
          )}
          {toolsUsed.map((t) => <ToolBadge key={t} tool={t} />)}
        </div>
      )}

      {/* Error banner */}
      {streamError && (
        <div style={{ padding: "8px 12px", background: "#fdf2f2", borderRadius: 6, color: "#c0392b", fontSize: 12, marginBottom: 8 }}>
          {streamError}
        </div>
      )}

      {/* Message list */}
      <div
        style={{
          flex: 1,
          overflowY: "auto",
          border: "0.5px solid var(--color-border-tertiary)",
          borderRadius: "var(--border-radius-lg)",
          padding: 16,
          marginBottom: 12,
          background: "var(--color-background-primary)",
        }}
      >
        {messages.map((m) => <Message key={m.id} msg={m} />)}
        {loading && messages[messages.length - 1]?.content === "" && <TypingIndicator />}
        <div ref={bottomRef} />
      </div>

      {/* Quick suggestions (only when no conversation yet) */}
      {messages.length <= 1 && (
        <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginBottom: 10 }}>
          {SUGGESTIONS.map((s) => (
            <button
              key={s}
              onClick={() => { setInput(s); }}
              style={{
                padding: "5px 12px",
                borderRadius: 20,
                border: "1px solid var(--color-border-tertiary)",
                background: "transparent",
                color: "var(--color-text-secondary)",
                cursor: "pointer",
                fontSize: 11,
              }}
            >
              {s}
            </button>
          ))}
        </div>
      )}

      {/* Input area */}
      <div style={{ display: "flex", gap: 8 }}>
        <textarea
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="Ask about revenue, pipeline, forecasts, reps, or quotas…"
          rows={2}
          style={{
            flex: 1,
            padding: "10px 12px",
            borderRadius: 8,
            border: "1px solid var(--color-border-tertiary)",
            background: "var(--color-background-secondary)",
            color: "var(--color-text-primary)",
            fontSize: 13,
            resize: "none",
            fontFamily: "inherit",
          }}
        />
        {loading ? (
          <button
            onClick={handleStop}
            style={{
              padding: "10px 16px",
              borderRadius: 8,
              border: "1px solid #D85A30",
              background: "transparent",
              color: "#D85A30",
              cursor: "pointer",
              fontSize: 12,
              whiteSpace: "nowrap",
            }}
          >
            Stop
          </button>
        ) : (
          <button
            onClick={sendMessage}
            disabled={!input.trim()}
            style={{
              padding: "10px 20px",
              borderRadius: 8,
              border: "none",
              background: input.trim() ? "#378ADD" : "var(--color-border-tertiary)",
              color: "#fff",
              cursor: input.trim() ? "pointer" : "default",
              fontSize: 13,
              fontWeight: 500,
              whiteSpace: "nowrap",
            }}
          >
            Send
          </button>
        )}
      </div>
    </div>
  );
}
