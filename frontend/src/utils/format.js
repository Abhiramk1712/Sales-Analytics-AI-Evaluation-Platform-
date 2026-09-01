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
