/** Shared formatting utilities */

export const API = import.meta.env.VITE_API_URL || "";

/** Safely coerce a value to a finite number, returning fallback if not possible. */
export function safeNumber(value, fallback = 0) {
  const n = Number(value);
  return Number.isFinite(n) ? n : fallback;
}

/** Format currency — handles null/NaN gracefully. */
export const fmt = (n) => {
  const v = safeNumber(n);
  if (v >= 1e6) return `$${(v / 1e6).toFixed(1)}M`;
  if (v >= 1e3) return `$${(v / 1e3).toFixed(0)}K`;
  return `$${v.toLocaleString()}`;
};

/** Format percentage — handles null/NaN gracefully. */
export const pct = (n) => {
  const v = safeNumber(n);
  return `${v.toFixed(1)}%`;
};

/** Format compact number (no currency sign). */
export const compactNum = (n) => {
  const v = safeNumber(n);
  if (v >= 1e6) return `${(v / 1e6).toFixed(1)}M`;
  if (v >= 1e3) return `${(v / 1e3).toFixed(0)}K`;
  return v.toLocaleString();
};

/** Safe currency format for tables/cards. */
export const safeCurrency = (n, fallback = "$0") => {
  const v = safeNumber(n, null);
  return v === null ? fallback : fmt(v);
};

/** Safe percentage for tables/cards. */
export const safePct = (n, fallback = "—") => {
  const v = safeNumber(n, null);
  return v === null ? fallback : pct(v);
};

export const withRefresh = (url, refreshKey) =>
  url.includes("?") ? `${url}&_r=${refreshKey}` : `${url}?_r=${refreshKey}`;

export const STAGE_COLORS = {
  Prospecting: "#B5D4F4",
  Qualification: "#85B7EB",
  Proposal: "#378ADD",
  Negotiation: "#185FA5",
  "Closed Won": "#1D9E75",
  "Closed Lost": "#D85A30",
};

export const PERSONA_COLORS = {
  "Top Performer": "#1D9E75",
  "High Volume": "#378ADD",
  "Rising Star": "#EF9F27",
  "Needs Coaching": "#D85A30",
};

/**
 * Convert the header's period label into the quarter key the payout API uses.
 *
 * The header speaks in labels ("this quarter", "Q2 2026", "YTD"); the payout
 * endpoints expect "YYYY-QN", "ytd" or "all-time". Without a translation the
 * Compensation tab kept its own period control, which drifted from the header
 * and defaulted to the current quarter regardless of what the user had chosen.
 *
 * Returns null when the label has no sensible quarter equivalent, so the caller
 * can keep its own default rather than being handed a wrong one.
 */
export function toPayoutPeriod(label) {
  if (!label) return null;
  const value = String(label).trim();
  const lower = value.toLowerCase();

  if (lower === "all time" || lower === "all-time") return "all-time";
  if (lower === "ytd") return "ytd";

  const quarterOf = (date) => `${date.getFullYear()}-Q${Math.ceil((date.getMonth() + 1) / 3)}`;
  const now = new Date();

  if (lower === "this quarter") return quarterOf(now);
  if (lower === "last quarter") {
    const d = new Date(now.getFullYear(), now.getMonth() - 3, 1);
    return quarterOf(d);
  }
  // A month maps to the quarter that contains it — the payout API has no
  // monthly grain, so this is the closest honest answer.
  if (lower === "this month") return quarterOf(now);
  if (lower === "last month") {
    const d = new Date(now.getFullYear(), now.getMonth() - 1, 1);
    return quarterOf(d);
  }
  if (lower === "last year") return `${now.getFullYear() - 1}-Q4`;

  // "Q2 2026" → "2026-Q2"
  const explicit = value.match(/^Q([1-4])\s+(\d{4})$/i);
  if (explicit) return `${explicit[2]}-Q${explicit[1]}`;

  // Already in the target shape.
  if (/^\d{4}-Q[1-4]$/.test(value)) return value;

  return null;
}
