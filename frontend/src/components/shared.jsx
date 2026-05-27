/** Shared UI components — enhanced */

// ── MetricCard ────────────────────────────────────────────────────────────
// Supports: label, value, sub, color, trend (+/-/neutral), icon (emoji/svg str)
export function MetricCard({ label, value, sub, color, trend, icon }) {
  const trendColor = trend === "up" ? "var(--color-green)" : trend === "down" ? "var(--color-red)" : "var(--color-text-tertiary)";
  const trendArrow = trend === "up" ? "↑" : trend === "down" ? "↓" : null;
  return (
    <div
      style={{
        background: "var(--color-background-primary)",
        borderRadius: "var(--border-radius-lg)",
        border: "1px solid var(--color-border-secondary)",
        padding: "16px 18px",
        minHeight: 100,
        boxShadow: "var(--shadow-sm)",
        display: "flex",
        flexDirection: "column",
        justifyContent: "space-between",
        transition: "box-shadow var(--transition-fast), transform var(--transition-fast)",
        cursor: "default",
      }}
      onMouseEnter={e => { e.currentTarget.style.boxShadow = "var(--shadow-md)"; e.currentTarget.style.transform = "translateY(-1px)"; }}
      onMouseLeave={e => { e.currentTarget.style.boxShadow = "var(--shadow-sm)"; e.currentTarget.style.transform = "translateY(0)"; }}
    >
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 10 }}>
        <div style={{ fontSize: 11, fontWeight: 600, color: "var(--color-text-secondary)", textTransform: "uppercase", letterSpacing: "0.6px" }}>
          {label}
        </div>
        {icon && <span style={{ fontSize: 15, opacity: 0.75 }}>{icon}</span>}
      </div>
      <div style={{ display: "flex", alignItems: "baseline", gap: 6 }}>
        <div style={{ fontSize: 24, fontWeight: 700, color: color || "var(--color-text-primary)", letterSpacing: "-0.5px", lineHeight: 1 }}>
          {value}
        </div>
        {trendArrow && (
          <span style={{ fontSize: 12, fontWeight: 600, color: trendColor }}>{trendArrow}</span>
        )}
      </div>
      {sub && (
        <div style={{ fontSize: 11, marginTop: 8, color: "var(--color-text-tertiary)", fontWeight: 500 }}>
          {sub}
        </div>
      )}
    </div>
  );
}

// ── Skeleton ──────────────────────────────────────────────────────────────
export function Skeleton({ h = 200, radius }) {
  return (
    <div
      className="skeleton-shimmer"
      style={{
        borderRadius: radius || "var(--border-radius-md)",
        height: h,
      }}
    />
  );
}

// ── Card ──────────────────────────────────────────────────────────────────
export function Card({ children, style = {}, padding }) {
  return (
    <div
      style={{
        border: "1px solid var(--color-border-secondary)",
        borderRadius: "var(--border-radius-lg)",
        background: "var(--color-background-primary)",
        padding: padding ?? 20,
        boxShadow: "var(--shadow-sm)",
        ...style,
      }}
    >
      {children}
    </div>
  );
}

// ── SectionTitle ──────────────────────────────────────────────────────────
export function SectionTitle({ children, sub }) {
  return (
    <div style={{ marginBottom: 14 }}>
      <div style={{ fontSize: 13, fontWeight: 700, color: "var(--color-text-primary)", letterSpacing: "0.1px" }}>{children}</div>
      {sub && <div style={{ fontSize: 11, color: "var(--color-text-secondary)", marginTop: 3 }}>{sub}</div>}
    </div>
  );
}

// ── StatusBadge ───────────────────────────────────────────────────────────
const BADGE_PRESETS = {
  success:  { bg: "var(--color-green-light)",  color: "var(--color-green)",  label: "Success" },
  warning:  { bg: "var(--color-amber-light)",  color: "var(--color-amber)",  label: "Warning" },
  error:    { bg: "var(--color-red-light)",    color: "var(--color-red)",    label: "Error"   },
  info:     { bg: "var(--color-blue-light)",   color: "var(--color-blue)",   label: "Info"    },
  neutral:  { bg: "var(--color-background-tertiary)", color: "var(--color-text-secondary)", label: "—" },
  high:     { bg: "var(--color-green-light)",  color: "var(--color-green)"  },
  medium:   { bg: "var(--color-amber-light)",  color: "var(--color-amber)"  },
  low:      { bg: "var(--color-red-light)",    color: "var(--color-red)"    },
};

export function StatusBadge({ status, label, size = "sm" }) {
  const preset = BADGE_PRESETS[status] || BADGE_PRESETS.neutral;
  const text = label ?? preset.label ?? status;
  const pad = size === "lg" ? "4px 10px" : "2px 8px";
  const fs  = size === "lg" ? 12 : 10;
  return (
    <span style={{ display: "inline-flex", alignItems: "center", padding: pad, borderRadius: 999, background: preset.bg, color: preset.color, fontSize: fs, fontWeight: 700, letterSpacing: "0.3px", whiteSpace: "nowrap" }}>
      {text}
    </span>
  );
}

// ── Divider ───────────────────────────────────────────────────────────────
export function Divider({ style = {} }) {
  return <hr style={{ border: "none", borderTop: "1px solid var(--color-border-secondary)", margin: "16px 0", ...style }} />;
}

// ── EmptyState ────────────────────────────────────────────────────────────
export function EmptyState({ icon = "📭", title = "No data", message }) {
  return (
    <div style={{ padding: "48px 24px", textAlign: "center", color: "var(--color-text-secondary)" }}>
      <div style={{ fontSize: 32, marginBottom: 12 }}>{icon}</div>
      <div style={{ fontWeight: 600, fontSize: 14, color: "var(--color-text-primary)", marginBottom: 6 }}>{title}</div>
      {message && <div style={{ fontSize: 13 }}>{message}</div>}
    </div>
  );
}

// ── ErrorMessage ──────────────────────────────────────────────────────────
export function ErrorMessage({ message }) {
  return (
    <div
      style={{
        display: "flex",
        alignItems: "flex-start",
        gap: 10,
        padding: "12px 16px",
        background: "var(--color-red-light)",
        border: "1px solid #fecaca",
        borderRadius: "var(--border-radius-md)",
        color: "var(--color-red)",
        fontSize: 13,
        fontWeight: 500,
      }}
    >
      <span style={{ fontSize: 15, lineHeight: 1.4 }}>⚠</span>
      <span>{message}</span>
    </div>
  );
}

// ── Table helpers ─────────────────────────────────────────────────────────
export function TableHeader({ columns }) {
  return (
    <thead>
      <tr>
        {columns.map((col) => (
          <th
            key={col}
            style={{
              textAlign: "left",
              fontWeight: 600,
              fontSize: 11,
              color: "var(--color-text-secondary)",
              textTransform: "uppercase",
              letterSpacing: "0.5px",
              padding: "10px 14px",
              borderBottom: "1px solid var(--color-border-secondary)",
              background: "var(--color-background-secondary)",
              whiteSpace: "nowrap",
            }}
          >
            {col}
          </th>
        ))}
      </tr>
    </thead>
  );
}

export const tdStyle = {
  padding: "10px 14px",
  fontSize: 13,
  borderBottom: "1px solid var(--color-border-tertiary)",
  verticalAlign: "middle",
};

// ── PaginationControls ────────────────────────────────────────────────────
export function PaginationControls({
  page,
  pageSize,
  totalItems,
  onPageChange,
  onPageSizeChange,
  pageSizeOptions = [5, 10, 20, 50],
}) {
  const totalPages = Math.max(1, Math.ceil((totalItems || 0) / Math.max(1, pageSize || 1)));
  const safePage   = Math.min(Math.max(1, page || 1), totalPages);
  const start      = totalItems === 0 ? 0 : (safePage - 1) * pageSize + 1;
  const end        = Math.min(totalItems, safePage * pageSize);

  const btnBase = {
    padding: "4px 10px",
    borderRadius: "var(--border-radius-sm)",
    border: "1px solid var(--color-border-secondary)",
    background: "var(--color-background-primary)",
    color: "var(--color-text-primary)",
    fontSize: 12,
    fontWeight: 500,
    transition: "background var(--transition-fast)",
  };

  return (
    <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 10, marginTop: 14, fontSize: 12, color: "var(--color-text-secondary)" }}>
      <div>{totalItems === 0 ? "No results" : `Showing ${start}–${end} of ${totalItems}`}</div>
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12 }}>
          Rows:
          <select
            value={pageSize}
            onChange={(e) => onPageSizeChange?.(Number(e.target.value))}
            style={{ ...btnBase, padding: "4px 6px" }}
          >
            {pageSizeOptions.map((size) => <option key={size} value={size}>{size}</option>)}
          </select>
        </label>
        <button onClick={() => onPageChange?.(1)} disabled={safePage <= 1} style={{ ...btnBase, opacity: safePage <= 1 ? 0.4 : 1 }}>«</button>
        <button onClick={() => onPageChange?.(safePage - 1)} disabled={safePage <= 1} style={{ ...btnBase, opacity: safePage <= 1 ? 0.4 : 1 }}>‹ Prev</button>
        <span style={{ fontWeight: 600, color: "var(--color-text-primary)", minWidth: 60, textAlign: "center" }}>
          {safePage} / {totalPages}
        </span>
        <button onClick={() => onPageChange?.(safePage + 1)} disabled={safePage >= totalPages} style={{ ...btnBase, opacity: safePage >= totalPages ? 0.4 : 1 }}>Next ›</button>
        <button onClick={() => onPageChange?.(totalPages)} disabled={safePage >= totalPages} style={{ ...btnBase, opacity: safePage >= totalPages ? 0.4 : 1 }}>»</button>
      </div>
    </div>
  );
}
