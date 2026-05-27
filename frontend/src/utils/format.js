/** Shared formatting utilities */

export const API = import.meta.env.VITE_API_URL || "";

export const fmt = (n) =>
  n >= 1e6 ? `$${(n / 1e6).toFixed(1)}M` : n >= 1e3 ? `$${(n / 1e3).toFixed(0)}K` : `$${n}`;

export const pct = (n) => `${Number(n).toFixed(1)}%`;

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
