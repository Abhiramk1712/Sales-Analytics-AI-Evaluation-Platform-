/**
 * AgentPage.jsx — AI chat with SSE streaming via /agent/chat/stream
 * Sprint 2.4
 */
import { useState, useRef, useCallback } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
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

const WELCOME = "Hi! I'm your sales intelligence assistant. Ask me about pipeline health, forecast accuracy, quota attainment, rep performance, or ARR trends.";

// ── Icons (inline SVG — no icon package in this project) ──────────────────

function BotAvatarIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
      <rect x="4" y="8" width="16" height="12" rx="3" stroke="#fff" strokeWidth="1.8" />
      <path d="M12 8V4" stroke="#fff" strokeWidth="1.8" strokeLinecap="round" />
      <circle cx="12" cy="3" r="1.4" fill="#fff" />
      <circle cx="9" cy="14" r="1.4" fill="#fff" />
      <circle cx="15" cy="14" r="1.4" fill="#fff" />
      <path d="M2 13h2M20 13h2" stroke="#fff" strokeWidth="1.8" strokeLinecap="round" />
    </svg>
  );
}

function SendIcon() {
  return (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
      <path d="M4 12L20 4L14 20L11 13L4 12Z" stroke="currentColor" strokeWidth="1.8" strokeLinejoin="round" />
    </svg>
  );
}

function StopIcon() {
  return (
    <svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor" xmlns="http://www.w3.org/2000/svg">
      <rect x="5" y="5" width="14" height="14" rx="2" />
    </svg>
  );
}

function TrashIcon() {
  return (
    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
      <path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function TypingIndicator() {
  return (
    <div style={{ display: "flex", gap: 4, alignItems: "center", padding: "10px 14px" }}>
      {[0, 1, 2].map((i) => (
        <span
          key={i}
          style={{
            width: 6,
            height: 6,
            borderRadius: "50%",
            background: "var(--color-accent-primary)",
            opacity: 0.6,
            animation: `agent-bounce 1.2s ${i * 0.15}s infinite`,
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
        border: "1px solid var(--color-border-secondary)",
        borderRadius: "var(--border-radius-md)",
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
                  stroke={s.color || "var(--color-accent-primary)"}
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
                  <Cell key={`pie-${idx}`} fill={row.fill || series[idx]?.color || "var(--color-accent-primary)"} />
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
                  fill={s.color || "var(--color-accent-primary)"}
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
                  fill={s.color || "var(--color-accent-primary)"}
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

// ── Markdown rendering ─────────────────────────────────────────────────────
// The assistant's real answers (backend/agent/prompts.py's system prompt asks
// for structured markdown) come back with headings, bold, tables and lists —
// render them properly instead of dumping raw "**"/"|"/"#" characters as
// plain text.

const markdownComponents = {
  h1: ({ children }) => (
    <div style={{ fontSize: 15, fontWeight: 700, margin: "2px 0 8px", color: "var(--color-text-primary)" }}>{children}</div>
  ),
  h2: ({ children }) => (
    <div style={{ fontSize: 13.5, fontWeight: 700, margin: "14px 0 6px", color: "var(--color-text-primary)" }}>{children}</div>
  ),
  h3: ({ children }) => (
    <div style={{ fontSize: 13, fontWeight: 600, margin: "10px 0 4px", color: "var(--color-text-primary)" }}>{children}</div>
  ),
  p: ({ children }) => <p style={{ margin: "0 0 8px", lineHeight: 1.6 }}>{children}</p>,
  ul: ({ children }) => <ul style={{ margin: "0 0 8px", paddingLeft: 18, lineHeight: 1.6 }}>{children}</ul>,
  ol: ({ children }) => <ol style={{ margin: "0 0 8px", paddingLeft: 18, lineHeight: 1.6 }}>{children}</ol>,
  li: ({ children }) => <li style={{ marginBottom: 2 }}>{children}</li>,
  strong: ({ children }) => <strong style={{ fontWeight: 700, color: "var(--color-text-primary)" }}>{children}</strong>,
  em: ({ children }) => <em style={{ color: "var(--color-text-secondary)" }}>{children}</em>,
  a: ({ children, href }) => (
    <a href={href} target="_blank" rel="noreferrer" style={{ color: "var(--color-accent-primary)", textDecoration: "underline" }}>
      {children}
    </a>
  ),
  hr: () => <hr style={{ border: "none", borderTop: "1px solid var(--color-border-secondary)", margin: "10px 0" }} />,
  blockquote: ({ children }) => (
    <div
      style={{
        borderLeft: "3px solid var(--color-accent-primary)",
        paddingLeft: 10,
        margin: "6px 0",
        color: "var(--color-text-secondary)",
      }}
    >
      {children}
    </div>
  ),
  code: ({ inline, children }) =>
    inline ? (
      <code
        style={{
          background: "var(--color-background-tertiary)",
          padding: "1px 5px",
          borderRadius: 4,
          fontFamily: "var(--font-mono)",
          fontSize: 12,
        }}
      >
        {children}
      </code>
    ) : (
      <pre
        style={{
          background: "var(--color-background-tertiary)",
          padding: 10,
          borderRadius: "var(--border-radius-sm)",
          overflowX: "auto",
          fontFamily: "var(--font-mono)",
          fontSize: 12,
          margin: "6px 0",
        }}
      >
        <code>{children}</code>
      </pre>
    ),
  table: ({ children }) => (
    <div style={{ overflowX: "auto", margin: "6px 0 10px", border: "1px solid var(--color-border-secondary)", borderRadius: "var(--border-radius-sm)" }}>
      <table style={{ borderCollapse: "collapse", width: "100%", fontSize: 12 }}>{children}</table>
    </div>
  ),
  thead: ({ children }) => <thead style={{ background: "var(--color-background-tertiary)" }}>{children}</thead>,
  th: ({ children }) => (
    <th
      style={{
        textAlign: "left",
        padding: "6px 10px",
        fontWeight: 700,
        color: "var(--color-text-primary)",
        borderBottom: "1px solid var(--color-border-secondary)",
        whiteSpace: "nowrap",
      }}
    >
      {children}
    </th>
  ),
  td: ({ children }) => (
    <td style={{ padding: "6px 10px", borderTop: "1px solid var(--color-border-tertiary)", verticalAlign: "top" }}>{children}</td>
  ),
};

function Markdown({ content }) {
  return (
    <ReactMarkdown remarkPlugins={[remarkGfm]} components={markdownComponents}>
      {content}
    </ReactMarkdown>
  );
}

// ── Avatars ─────────────────────────────────────────────────────────────

function Avatar({ isUser }) {
  return (
    <div
      style={{
        flexShrink: 0,
        width: 26,
        height: 26,
        borderRadius: "50%",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        background: isUser ? "var(--color-text-primary)" : "var(--color-accent-primary)",
        color: "#fff",
        fontSize: 11,
        fontWeight: 700,
      }}
    >
      {isUser ? "U" : <BotAvatarIcon />}
    </div>
  );
}

function Message({ msg }) {
  const isUser = msg.role === "user";
  return (
    <div
      style={{
        display: "flex",
        flexDirection: isUser ? "row-reverse" : "row",
        gap: 8,
        alignItems: "flex-start",
        marginBottom: 14,
      }}
    >
      <Avatar isUser={isUser} />
      <div
        style={{
          maxWidth: "78%",
          padding: isUser ? "9px 14px" : "12px 15px",
          borderRadius: isUser ? "14px 14px 3px 14px" : "14px 14px 14px 3px",
          background: isUser ? "var(--color-accent-primary)" : "var(--color-background-primary)",
          border: isUser ? "none" : "1px solid var(--color-border-secondary)",
          boxShadow: isUser ? "none" : "var(--shadow-xs)",
          color: isUser ? "#fff" : "var(--color-text-primary)",
          fontSize: 13,
        }}
      >
        {isUser ? (
          <div style={{ lineHeight: 1.6, whiteSpace: "pre-wrap" }}>{msg.content}</div>
        ) : (
          <>
            <Markdown content={msg.content || (msg.streaming ? "" : "")} />
            {msg.streaming && <span style={{ opacity: 0.5 }}>▌</span>}
          </>
        )}
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
        background: "var(--color-background-tertiary)",
        fontSize: 10,
        color: "var(--color-text-secondary)",
        border: "1px solid var(--color-border-tertiary)",
        marginRight: 4,
      }}
    >
      {tool}
    </span>
  );
}

function qualityColor(level) {
  if (level === "high") return "var(--color-green)";
  if (level === "medium") return "var(--color-amber)";
  return "var(--color-red)";
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
        borderRadius: "var(--border-radius-sm)",
        border: `1px solid ${color}33`,
        background: `${color}0f`,
      }}
    >
      <div style={{ fontSize: 11, fontWeight: 700, color }}>
        Trust {level.toUpperCase()} · {score}/100
      </div>
      <div style={{ fontSize: 11, marginTop: 4, color: "var(--color-text-secondary)" }}>
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

  const resetConversation = () => {
    abortRef.current?.abort();
    setMessages([{ id: "welcome", role: "assistant", content: WELCOME }]);
    setInput("");
    setLoading(false);
    setToolsUsed([]);
    setIntent("");
    setAnswerQuality(null);
    setStreamError(null);
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
              ? { ...m, content: "_Cancelled._", streaming: false }
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
                ? { ...m, content: "_Connection error — see banner above._", streaming: false }
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
      <style>{`
        @keyframes agent-bounce {
          0%, 60%, 100% { transform: translateY(0); opacity: 0.5; }
          30% { transform: translateY(-3px); opacity: 1; }
        }
      `}</style>

      {/* Header row: title + metadata badges + clear */}
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "2px 0 10px", gap: 8, flexWrap: "wrap" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap" }}>
          {intent && (
            <span style={{ fontSize: 10, color: "var(--color-text-secondary)", marginRight: 2 }}>
              Intent: <strong style={{ color: "var(--color-text-primary)" }}>{intent}</strong>
            </span>
          )}
          {answerQuality && (
            <span style={{ fontSize: 10, color: qualityColor(answerQuality.level), fontWeight: 700 }}>
              Trust: {String(answerQuality.level || "medium").toUpperCase()} {Number(answerQuality.score || 0)}/100
            </span>
          )}
          {toolsUsed.map((t) => <ToolBadge key={t} tool={t} />)}
        </div>
        {messages.length > 1 && (
          <button
            onClick={resetConversation}
            title="Clear conversation"
            style={{
              display: "flex",
              alignItems: "center",
              gap: 5,
              padding: "4px 9px",
              borderRadius: "var(--border-radius-sm)",
              border: "1px solid var(--color-border-secondary)",
              background: "var(--color-background-primary)",
              color: "var(--color-text-secondary)",
              fontSize: 11,
            }}
          >
            <TrashIcon /> Clear
          </button>
        )}
      </div>

      {/* Error banner */}
      {streamError && (
        <div
          style={{
            padding: "8px 12px",
            background: "var(--color-red-light)",
            border: "1px solid var(--color-red)33",
            borderRadius: "var(--border-radius-sm)",
            color: "var(--color-red)",
            fontSize: 12,
            marginBottom: 8,
          }}
        >
          {streamError}
        </div>
      )}

      {/* Message list */}
      <div
        style={{
          flex: 1,
          overflowY: "auto",
          border: "1px solid var(--color-border-secondary)",
          borderRadius: "var(--border-radius-lg)",
          padding: 16,
          marginBottom: 12,
          background: "var(--color-background-secondary)",
          boxShadow: "var(--shadow-sm)",
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
                border: "1px solid var(--color-border-secondary)",
                background: "var(--color-background-primary)",
                color: "var(--color-text-secondary)",
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
            borderRadius: "var(--border-radius-md)",
            border: "1px solid var(--color-border-secondary)",
            background: "var(--color-background-primary)",
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
              display: "flex",
              alignItems: "center",
              gap: 6,
              padding: "10px 16px",
              borderRadius: "var(--border-radius-md)",
              border: "1px solid var(--color-red)",
              background: "transparent",
              color: "var(--color-red)",
              fontSize: 12,
              whiteSpace: "nowrap",
            }}
          >
            <StopIcon /> Stop
          </button>
        ) : (
          <button
            onClick={sendMessage}
            disabled={!input.trim()}
            style={{
              display: "flex",
              alignItems: "center",
              gap: 6,
              padding: "10px 20px",
              borderRadius: "var(--border-radius-md)",
              border: "none",
              background: input.trim() ? "var(--color-accent-primary)" : "var(--color-border-secondary)",
              color: "#fff",
              cursor: input.trim() ? "pointer" : "default",
              fontSize: 13,
              fontWeight: 600,
              whiteSpace: "nowrap",
            }}
          >
            Send <SendIcon />
          </button>
        )}
      </div>
    </div>
  );
}
