import { useMemo, useState, useEffect, useRef } from "react";
import { Bar, BarChart, CartesianGrid, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { PaginationControls } from "../components/shared";

const API = import.meta.env.VITE_API_URL || "";
const fmt = (n) => n >= 1e6 ? `$${(n/1e6).toFixed(1)}M` : n >= 1e3 ? `$${(n/1e3).toFixed(0)}K` : `$${Number(n||0).toFixed(0)}`;
const pct = (n) => `${Number(n||0).toFixed(1)}%`;
const clamp = (value, min, max) => Math.min(max, Math.max(min, value));

// ── Region system ─────────────────────────────────────────────────────────────
// Every distinct region string from the backend gets an SVG anchor (viewBox
// 1000×500) and a display color. US domestic sub-regions are spread across the
// NA continent shape (path spans roughly x:95–310, y:70–240).

// Known geographic anchors keyed by UPPER-CASE region string.
// Add new rows here whenever new region names appear in the data.
const REGION_ANCHORS = {
  // ── US domestic sub-regions ───────────────────────────────────────────
  "NORTHEAST REGION": { cx: 278, cy:  98, label: "Northeast",  color: "#378ADD" },
  "SOUTHEAST REGION": { cx: 268, cy: 205, label: "Southeast",  color: "#2ECC9A" },
  "MIDWEST REGION":   { cx: 200, cy: 118, label: "Midwest",    color: "#8B5CF6" },
  "CENTRAL REGION":   { cx: 190, cy: 175, label: "Central",    color: "#EF9F27" },
  "NORTHWEST REGION": { cx: 122, cy:  90, label: "Northwest",  color: "#E879F9" },
  "SOUTHWEST REGION": { cx: 138, cy: 198, label: "Southwest",  color: "#F87171" },
  // ── Canonical / international ─────────────────────────────────────────
  "NA":               { cx: 200, cy: 150, label: "N. America", color: "#378ADD" },
  "NAMER":            { cx: 200, cy: 150, label: "N. America", color: "#378ADD" },
  "EMEA":             { cx: 490, cy: 165, label: "EMEA",       color: "#1D9E75" },
  "APAC":             { cx: 760, cy: 120, label: "APAC",       color: "#8B5CF6" },
  "LATAM":            { cx: 255, cy: 320, label: "LATAM",      color: "#EF9F27" },
};

const FALLBACK_ANCHOR = { cx: 550, cy: 390, label: "Other", color: "#94A3B8" };

// Resolve the anchor for any raw region string from the API.
function getAnchor(regionStr) {
  const key = String(regionStr || "").toUpperCase().trim();
  if (REGION_ANCHORS[key]) return { ...REGION_ANCHORS[key], _key: key };
  // Fuzzy fallbacks for unrecognised strings
  if (key.includes("NORTH") && (key.includes("EAST") || key.includes("NE")))  return { ...REGION_ANCHORS["NORTHEAST REGION"], _key: key };
  if (key.includes("SOUTH") && (key.includes("EAST") || key.includes("SE")))  return { ...REGION_ANCHORS["SOUTHEAST REGION"], _key: key };
  if (key.includes("NORTH") && (key.includes("WEST") || key.includes("NW")))  return { ...REGION_ANCHORS["NORTHWEST REGION"], _key: key };
  if (key.includes("SOUTH") && (key.includes("WEST") || key.includes("SW")))  return { ...REGION_ANCHORS["SOUTHWEST REGION"], _key: key };
  if (key.includes("MIDWEST") || key.includes("MID WEST"))                    return { ...REGION_ANCHORS["MIDWEST REGION"],   _key: key };
  if (key.includes("CENTRAL"))                                                 return { ...REGION_ANCHORS["CENTRAL REGION"],   _key: key };
  if (key.includes("EUROPE") || key.includes("EUR"))  return { ...REGION_ANCHORS["EMEA"],  _key: key };
  if (key.includes("ASIA")   || key.includes("APAC")) return { ...REGION_ANCHORS["APAC"],  _key: key };
  if (key.includes("LATIN")  || key.includes("LATAM"))return { ...REGION_ANCHORS["LATAM"], _key: key };
  if (key.includes("NORTH AM") || key.includes("US") || key.startsWith("NA"))  return { ...REGION_ANCHORS["NA"], _key: key };
  return { ...FALLBACK_ANCHOR, _key: key };
}

function regionColor(r) { return getAnchor(r).color; }

function repAttainmentColor(a) {
  if (a >= 100) return "#1D9E75";
  if (a >= 75)  return "#EF9F27";
  return "#D85A30";
}

function initials(name) {
  if (!name) return "?";
  const parts = String(name).trim().split(/\s+/);
  return parts.length >= 2 ? (parts[0][0] + parts[1][0]).toUpperCase() : String(name)[0].toUpperCase();
}

// ── Data fetching ─────────────────────────────────────────────────────────────
function useFetch(url) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  useEffect(() => {
    if (!url) { setData(null); setLoading(false); return; }
    const controller = new AbortController();
    setLoading(true); setError(null);
    fetch(API + url, { signal: controller.signal })
      .then(async (r) => {
        const body = await r.json().catch(() => ({}));
        if (!r.ok) throw new Error(body?.detail || `Request failed (${r.status})`);
        return body;
      })
      .then(d => { if (!controller.signal.aborted) { setData(d); setLoading(false); } })
      .catch(e => { if (!controller.signal.aborted) { setError(e.message); setLoading(false); } });
    return () => controller.abort();
  }, [url]);
  return { data, loading, error };
}

// ── Skeleton ──────────────────────────────────────────────────────────────────
function Skeleton({ h = 120, r = 12 }) {
  return (
    <div style={{
      background: "linear-gradient(90deg, var(--color-background-secondary) 25%, var(--color-background-tertiary) 50%, var(--color-background-secondary) 75%)",
      backgroundSize: "200% 100%",
      borderRadius: r,
      height: h,
      animation: "shimmer 1.6s infinite",
    }} />
  );
}

// ── World Map SVG (simplified continent paths) ────────────────────────────────
// Viewbox 0 0 1000 500. Paths are approximate continent silhouettes.
const CONTINENT_PATHS = [
  { id:"na",   d:"M120,70 L280,70 L310,120 L300,200 L240,240 L200,230 L140,200 L100,150 L95,110 Z", label:"North America" },
  { id:"sa",   d:"M220,260 L280,250 L300,280 L290,370 L250,400 L220,380 L200,320 L210,280 Z", label:"South America" },
  { id:"eu",   d:"M430,60 L510,55 L530,90 L520,130 L480,140 L450,130 L430,100 Z", label:"Europe" },
  { id:"af",   d:"M430,150 L510,145 L540,180 L545,280 L510,330 L470,340 L440,310 L420,250 L415,180 Z", label:"Africa" },
  { id:"me",   d:"M530,110 L590,100 L610,130 L600,165 L560,175 L530,155 Z", label:"Middle East" },
  { id:"asia", d:"M600,50 L820,45 L850,90 L860,170 L820,200 L760,195 L700,180 L650,200 L610,190 L580,160 L570,100 Z", label:"Asia" },
  { id:"apac", d:"M760,220 L840,215 L870,250 L855,310 L810,330 L770,310 L750,270 Z", label:"Oceania" },
];

function WorldMap({ points, selectedId, onSelect }) {
  const [hovered, setHovered] = useState(null);
  const [tooltip, setTooltip] = useState(null);
  const svgRef = useRef(null);

  return (
    <div style={{ position: "relative", width: "100%", paddingBottom: "50%", borderRadius: 12, overflow: "hidden" }}>
      <svg
        ref={svgRef}
        viewBox="0 0 1000 500"
        style={{
          position: "absolute", inset: 0, width: "100%", height: "100%",
          background: "radial-gradient(ellipse at 30% 40%, rgba(55,138,221,0.12) 0%, transparent 60%), radial-gradient(ellipse at 75% 60%, rgba(29,158,117,0.1) 0%, transparent 60%), #0B1929",
        }}
      >
        <defs>
          <filter id="glow">
            <feGaussianBlur stdDeviation="3" result="blur" />
            <feMerge><feMergeNode in="blur" /><feMergeNode in="SourceGraphic" /></feMerge>
          </filter>
          {/* Dot rings */}
          <radialGradient id="pulse" cx="50%" cy="50%" r="50%">
            <stop offset="0%" stopColor="white" stopOpacity="0.9" />
            <stop offset="100%" stopColor="white" stopOpacity="0" />
          </radialGradient>
        </defs>

        {/* Grid lines */}
        {[100,200,300,400].map(y => (
          <line key={y} x1="0" y1={y} x2="1000" y2={y} stroke="rgba(255,255,255,0.04)" strokeWidth="1" />
        ))}
        {[125,250,375,500,625,750,875].map(x => (
          <line key={x} x1={x} y1="0" x2={x} y2="500" stroke="rgba(255,255,255,0.04)" strokeWidth="1" />
        ))}

        {/* Continent fills */}
        {CONTINENT_PATHS.map(c => (
          <path key={c.id} d={c.d} fill="rgba(255,255,255,0.07)" stroke="rgba(255,255,255,0.12)" strokeWidth="1" />
        ))}

        {/* Region label badges — one per distinct region present in points */}
        {(() => {
          // Build a deduplicated map of anchor key → anchor for regions that
          // actually have dots on the map.
          const seen = new Map();
          points.forEach(pt => {
            const anchor = pt._anchor || getAnchor(pt.region);
            const rawKey = String(pt.region || "").toUpperCase().trim();
            if (!seen.has(rawKey)) seen.set(rawKey, { anchor, label: anchor.label, count: 0 });
            seen.get(rawKey).count += 1;
          });
          return Array.from(seen.values()).map(({ anchor, label, count }, i) => {
            const { cx: rcx, cy: rcy, color } = anchor;
            // Place badge above the cluster anchor
            const by = rcy - 26;
            const textW = label.length * 6.5 + 16;
            return (
              <g key={i} pointerEvents="none">
                <rect x={rcx - textW / 2} y={by - 11} width={textW} height={20}
                  rx="10" fill="rgba(11,25,41,0.82)" stroke={color}
                  strokeWidth="1" strokeOpacity="0.7" />
                <text x={rcx} y={by + 1}
                  textAnchor="middle" dominantBaseline="middle"
                  fill={color} fontSize="10" fontWeight="700"
                  fontFamily="Inter, sans-serif">
                  {label}
                </text>
                <text x={rcx + textW / 2 - 10} y={by - 3}
                  textAnchor="middle" dominantBaseline="middle"
                  fill={color} fontSize="8" fontWeight="600"
                  fontFamily="Inter, sans-serif" opacity="0.8">
                  {count}
                </text>
                <line x1={rcx} y1={by + 9} x2={rcx} y2={rcy - 6}
                  stroke={color} strokeWidth="1" strokeDasharray="3 2" strokeOpacity="0.35" />
              </g>
            );
          });
        })()}

        {/* Territory dots */}
        {points.map((pt) => {
          const isSelected = pt.id === selectedId;
          const isHov = pt.id === hovered;
          const color = (pt._anchor || getAnchor(pt.region)).color;
          const cx = pt.svgX !== undefined ? pt.svgX : 500;
          const cy = pt.svgY !== undefined ? pt.svgY : 250;
          return (
            <g
              key={pt.id}
              onClick={() => onSelect(pt.id)}
              onMouseEnter={(e) => {
                setHovered(pt.id);
                setTooltip({ id: pt.id, name: pt.name, region: pt.region, segment: pt.segment, mx: e.clientX, my: e.clientY });
              }}
              onMouseMove={(e) => setTooltip(prev => prev ? { ...prev, mx: e.clientX, my: e.clientY } : null)}
              onMouseLeave={() => { setHovered(null); setTooltip(null); }}
              style={{ cursor: "pointer" }}
            >
              {/* Pulse ring */}
              {isSelected && (
                <circle cx={cx} cy={cy} r="18" fill="none" stroke={color} strokeWidth="1.5" opacity="0.4">
                  <animate attributeName="r" from="12" to="24" dur="1.8s" repeatCount="indefinite" />
                  <animate attributeName="opacity" from="0.6" to="0" dur="1.8s" repeatCount="indefinite" />
                </circle>
              )}
              {isHov && !isSelected && (
                <circle cx={cx} cy={cy} r="12" fill={color} opacity="0.2" />
              )}
              {/* Main dot */}
              <circle
                cx={cx} cy={cy}
                r={isSelected ? 9 : isHov ? 7 : 5}
                fill={color}
                stroke="rgba(255,255,255,0.85)"
                strokeWidth={isSelected ? 2 : 1}
                filter={isSelected || isHov ? "url(#glow)" : undefined}
                style={{ transition: "r 0.15s ease" }}
              />
              {/* Inner dot for selected */}
              {isSelected && <circle cx={cx} cy={cy} r="3" fill="white" />}
            </g>
          );
        })}
      </svg>

      {/* Floating tooltip */}
      {tooltip && (
        <div style={{
          position: "fixed",
          left: tooltip.mx + 14,
          top: tooltip.my - 36,
          pointerEvents: "none",
          background: "rgba(11,25,41,0.95)",
          border: `1px solid ${regionColor(tooltip.region)}44`,
          borderRadius: 8,
          padding: "6px 10px",
          fontSize: 12,
          color: "#F8FBFF",
          zIndex: 999,
          backdropFilter: "blur(8px)",
          boxShadow: "0 8px 24px rgba(0,0,0,0.3)",
          whiteSpace: "nowrap",
        }}>
          <div style={{ fontWeight: 600 }}>{tooltip.name}</div>
          <div style={{ fontSize: 10, color: regionColor(tooltip.region), marginTop: 2 }}>
            {tooltip.region} · {tooltip.segment}
          </div>
        </div>
      )}
    </div>
  );
}

// ── KPI Card ──────────────────────────────────────────────────────────────────
function KpiCard({ label, value, sub, accent = "#378ADD", icon, trend }) {
  const trendIcon = trend === "up" ? "↑" : trend === "down" ? "↓" : null;
  const trendColor = trend === "up" ? "#1D9E75" : trend === "down" ? "#D85A30" : accent;
  return (
    <div style={{
      borderRadius: 14,
      padding: "16px 18px",
      background: "var(--color-background-primary)",
      border: "1px solid var(--color-border-secondary)",
      boxShadow: "0 2px 10px rgba(0,0,0,0.04)",
      position: "relative",
      overflow: "hidden",
    }}>
      {/* Accent stripe */}
      <div style={{ position: "absolute", left: 0, top: 0, bottom: 0, width: 3, borderRadius: "14px 0 0 14px", background: accent }} />
      <div style={{ fontSize: 11, color: "var(--color-text-secondary)", textTransform: "uppercase", letterSpacing: "0.5px", marginBottom: 6, paddingLeft: 2 }}>
        {icon && <span style={{ marginRight: 5 }}>{icon}</span>}{label}
      </div>
      <div style={{ display: "flex", alignItems: "baseline", gap: 6 }}>
        <div style={{ fontSize: 26, fontWeight: 700, color: accent, letterSpacing: "-0.5px", lineHeight: 1 }}>{value}</div>
        {trendIcon && <span style={{ fontSize: 13, color: trendColor, fontWeight: 600 }}>{trendIcon}</span>}
      </div>
      {sub && <div style={{ marginTop: 6, fontSize: 11, color: "var(--color-text-secondary)" }}>{sub}</div>}
    </div>
  );
}

// ── Hygiene bar with percentage fill ─────────────────────────────────────────
function HygieneBar({ label, value, color, maxValue, icon }) {
  const pct = maxValue > 0 ? clamp((Number(value || 0) / maxValue) * 100, 0, 100) : 0;
  return (
    <div style={{
      borderRadius: 12,
      padding: "12px 14px",
      background: "var(--color-background-primary)",
      border: "1px solid var(--color-border-secondary)",
    }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
          <span style={{ fontSize: 14 }}>{icon}</span>
          <span style={{ fontSize: 12, fontWeight: 500, color: "var(--color-text-primary)" }}>{label}</span>
        </div>
        <span style={{
          fontSize: 14, fontWeight: 700, color,
          background: `${color}18`, borderRadius: 6, padding: "1px 7px",
        }}>{Number(value || 0)}</span>
      </div>
      <div style={{ height: 8, borderRadius: 999, background: "var(--color-background-tertiary)", overflow: "hidden" }}>
        <div style={{
          width: `${pct}%`, height: "100%",
          background: `linear-gradient(90deg, ${color}99, ${color})`,
          borderRadius: 999,
          transition: "width 0.5s cubic-bezier(0.4,0,0.2,1)",
        }} />
      </div>
      <div style={{ marginTop: 4, fontSize: 10, color: "var(--color-text-tertiary)" }}>{pct.toFixed(0)}% of territory max</div>
    </div>
  );
}

// ── Territory card in sidebar ─────────────────────────────────────────────────
function TerritoryCard({ territory, isSelected, onClick }) {
  const color = regionColor(territory.region);
  return (
    <button
      onClick={onClick}
      style={{
        textAlign: "left", width: "100%",
        padding: "10px 12px",
        borderRadius: 10,
        border: isSelected ? `1.5px solid ${color}` : "1px solid var(--color-border-secondary)",
        background: isSelected ? `${color}12` : "var(--color-background-primary)",
        color: "var(--color-text-primary)",
        cursor: "pointer",
        fontFamily: "var(--font-sans)",
        transition: "all 0.15s ease",
        display: "flex",
        alignItems: "center",
        gap: 10,
      }}
    >
      <div style={{
        width: 10, height: 10, borderRadius: "50%",
        background: color, flexShrink: 0,
        boxShadow: isSelected ? `0 0 0 3px ${color}33` : "none",
      }} />
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontWeight: isSelected ? 600 : 500, fontSize: 12, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
          {territory.name}
        </div>
        <div style={{ marginTop: 2, fontSize: 10, color: "var(--color-text-secondary)" }}>
          <span style={{ color, fontWeight: 500 }}>{territory.region || "N/A"}</span>
          {" · "}
          {territory.segment || "N/A"}
        </div>
      </div>
      {isSelected && <span style={{ fontSize: 10, color, flexShrink: 0 }}>▶</span>}
    </button>
  );
}

// ── Rep avatar row ────────────────────────────────────────────────────────────
function RepRow({ rep, rank }) {
  const attainment = Number(rep.attainment_pct || 0);
  const color = repAttainmentColor(attainment);
  const progress = clamp((attainment / 150) * 100, 0, 100);
  const avatarColors = ["#378ADD", "#1D9E75", "#8B5CF6", "#EF9F27", "#D85A30"];
  const avatarBg = avatarColors[(rank - 1) % avatarColors.length];
  return (
    <tr style={{ borderBottom: "1px solid var(--color-border-secondary)", transition: "background 0.1s" }}
      onMouseEnter={e => e.currentTarget.style.background = "var(--color-background-secondary)"}
      onMouseLeave={e => e.currentTarget.style.background = ""}
    >
      <td style={{ padding: "10px 10px", verticalAlign: "middle" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 9 }}>
          <div style={{
            width: 30, height: 30, borderRadius: "50%", flexShrink: 0,
            background: `linear-gradient(135deg, ${avatarBg}dd, ${avatarBg}88)`,
            color: "white", fontSize: 11, fontWeight: 700,
            display: "flex", alignItems: "center", justifyContent: "center",
            border: "1.5px solid white",
            boxShadow: "0 2px 6px rgba(0,0,0,0.12)",
          }}>
            {initials(rep.name)}
          </div>
          <div>
            <div style={{ fontSize: 12, fontWeight: 500 }}>{rep.name || "Rep"}</div>
            <div style={{ fontSize: 10, color: "var(--color-text-tertiary)", marginTop: 1 }}>{rep.deals_won ?? 0} deals</div>
          </div>
        </div>
      </td>
      <td style={{ padding: "10px 10px", textAlign: "right", fontSize: 12, fontWeight: 600 }}>{fmt(rep.revenue || 0)}</td>
      <td style={{ padding: "10px 10px", textAlign: "right", fontSize: 12 }}>{rep.deals_won ?? "—"}</td>
      <td style={{ padding: "10px 14px", textAlign: "right" }}>
        <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end", gap: 4 }}>
          <span style={{
            fontSize: 11, fontWeight: 700, color,
            background: `${color}18`, borderRadius: 6, padding: "1px 7px",
          }}>{pct(attainment)}</span>
          <div style={{ width: 80, height: 5, borderRadius: 999, background: "var(--color-background-tertiary)", overflow: "hidden" }}>
            <div style={{
              width: `${progress}%`, height: "100%",
              background: `linear-gradient(90deg, ${color}88, ${color})`,
              borderRadius: 999, transition: "width 0.4s ease",
            }} />
          </div>
        </div>
      </td>
    </tr>
  );
}

// ── Territory narrative ───────────────────────────────────────────────────────
function territoryNarrative(perfData) {
  if (!perfData) return "Select a territory to see contextual recommendations.";
  if (perfData.assigned_reps === 0) return "No reps are currently assigned. Start with ownership coverage and set quota and activity expectations.";
  if (Number(perfData.win_rate || 0) >= 30 && Number(perfData.open_pipeline || 0) > 0)
    return "Conversion quality is strong. Focus on pipeline depth and stage progression to sustain momentum.";
  if (Number(perfData.win_rate || 0) < 20)
    return "Win rate is under pressure. Tighten qualification criteria and enforce next-step cadence on high-probability deals.";
  return "Territory is stable but not yet efficient. Improve close-date discipline and rebalance effort toward late-stage opportunities.";
}

// ── Custom bar tooltip ────────────────────────────────────────────────────────
function CustomBarTooltip({ active, payload, label }) {
  if (!active || !payload?.length) return null;
  const v = payload[0]?.value;
  return (
    <div style={{
      background: "rgba(11,25,41,0.95)", border: "1px solid rgba(55,138,221,0.3)",
      borderRadius: 8, padding: "8px 12px", fontSize: 12, color: "#F8FBFF",
      boxShadow: "0 8px 24px rgba(0,0,0,0.3)",
    }}>
      <div style={{ fontWeight: 600, marginBottom: 3 }}>{label}</div>
      <div style={{ color: "#7DD3FC" }}>{fmt(Number(v || 0))}</div>
    </div>
  );
}

// ═════════════════════════════════════════════════════════════════════════════
export default function TerritoriesPage({ refreshKey, period, activeCompany }) {
  const [selectedTerritory, setSelectedTerritory] = useState(null);
  const [search, setSearch] = useState("");
  const [repPage, setRepPage] = useState(1);
  const [repPageSize, setRepPageSize] = useState(10);
  const [activeSection, setActiveSection] = useState("overview"); // overview | reps | hygiene

  const companyQuery = encodeURIComponent(activeCompany || "");
  const { data: territories, loading, error: territoriesError } = useFetch(`/territories?_r=${refreshKey}&company=${companyQuery}`);

  const perfUrl = useMemo(() => {
    if (!selectedTerritory) return null;
    const params = new URLSearchParams();
    if (period) params.set("period", period);
    params.set("_r", String(refreshKey));
    if (activeCompany) params.set("company", activeCompany);
    return `/territories/${selectedTerritory}/performance?${params.toString()}`;
  }, [selectedTerritory, period, refreshKey, activeCompany]);

  const { data: perfData, loading: perfLoading } = useFetch(perfUrl);

  useEffect(() => {
    setSelectedTerritory(null);
    setSearch("");
    setRepPage(1);
  }, [refreshKey, activeCompany]);

  useEffect(() => {
    const all = territories?.territories || [];
    if (!all.length) { setSelectedTerritory(null); return; }
    if (!selectedTerritory || !all.some(t => t.id === selectedTerritory)) {
      setSelectedTerritory(all[0].id);
      setRepPage(1);
    }
  }, [territories, selectedTerritory]);

  const allTerritories = territories?.territories || [];

  const filteredTerritories = useMemo(() => {
    const needle = search.trim().toLowerCase();
    if (!needle) return allTerritories;
    return allTerritories.filter(t =>
      `${t.name || ""} ${t.region || ""} ${t.segment || ""}`.toLowerCase().includes(needle)
    );
  }, [allTerritories, search]);

  // Map points — each territory placed at its region's SVG anchor, then
  // fanned out in a small hex-grid so dots don't overlap.
  const mapPoints = useMemo(() => {
    const counters = {};
    return filteredTerritories.map(t => {
      const anchor = getAnchor(t.region);
      const key = anchor._key;
      const idx = counters[key] || 0;
      counters[key] = idx + 1;
      const col = idx % 4;
      const row = Math.floor(idx / 4);
      // Hex-grid offset: even rows offset by half a step
      const offsetX = (col - 1.5) * 24 + (row % 2) * 12;
      const offsetY = row * 22;
      return {
        ...t,
        _anchor: anchor,
        svgX: clamp(anchor.cx + offsetX, 14, 980),
        svgY: clamp(anchor.cy + offsetY, 12, 480),
      };
    });
  }, [filteredTerritories]);

  const selectedMeta = useMemo(
    () => allTerritories.find(t => t.id === selectedTerritory) || null,
    [allTerritories, selectedTerritory]
  );

  const regionMix = useMemo(() => {
    const mix = new Map();
    allTerritories.forEach(t => {
      const key = String(t.region || "").toUpperCase().trim();
      const anchor = getAnchor(t.region);
      if (!mix.has(key)) mix.set(key, { label: anchor.label, color: anchor.color, count: 0 });
      mix.get(key).count += 1;
    });
    return [...mix.values()].sort((a, b) => b.count - a.count).slice(0, 6);
  }, [allTerritories]);

  const reps = perfData?.reps || [];
  const repPages = Math.max(1, Math.ceil(reps.length / Math.max(1, repPageSize)));
  const safeRepPage = Math.min(repPage, repPages);
  const pagedReps = reps.slice((safeRepPage - 1) * repPageSize, safeRepPage * repPageSize);

  const repChartData = reps.slice(0, 10).map(r => ({
    name: r.name ? r.name.split(" ")[0] : "Rep",
    revenue: Number(r.revenue || 0),
    attainment: Number(r.attainment_pct || 0),
  }));

  const topPerformers = useMemo(
    () => [...reps].sort((a, b) => Number(b.revenue || 0) - Number(a.revenue || 0)).slice(0, 3),
    [reps]
  );

  const warnings = perfData?.warnings || [];
  const fallbackWarning = warnings.find(w => String(w).toLowerCase().includes("inferred")) || null;
  const criticalWarnings = warnings.filter(w => w !== fallbackWarning);

  const hygieneRows = [
    { key: "overdue_count",             label: "Overdue Deals",        color: "#D85A30", icon: "🔴" },
    { key: "missing_close_date_count",  label: "Missing Close Date",   color: "#EF9F27", icon: "📅" },
    { key: "high_prob_early_stage_count",label: "High-Prob Early Stage",color: "#378ADD", icon: "⚡" },
  ];
  const maxHygiene = Math.max(1, ...hygieneRows.map(r => Number(perfData?.pipeline_hygiene?.[r.key] || 0)));

  const selColor = regionColor(selectedMeta?.region || "OTHER");
  const TABS = ["overview", "reps", "hygiene"];
  const TAB_LABELS = { overview: "📊 Overview", reps: "👤 Reps", hygiene: "🔧 Hygiene" };

  // ── render ─────────────────────────────────────────────────────────────────
  return (
    <div style={{ display: "grid", gap: 16 }}>

      {/* ── CSS for shimmer + transitions ── */}
      <style>{`
        @keyframes shimmer { 0%{background-position:200% 0} 100%{background-position:-200% 0} }
        @keyframes fadeIn { from{opacity:0;transform:translateY(6px)} to{opacity:1;transform:none} }
        .territory-panel { animation: fadeIn 0.25s ease; }
      `}</style>

      {/* ── Hero header ─────────────────────────────────────────────────── */}
      <div style={{
        borderRadius: 16,
        padding: "20px 24px",
        background: "linear-gradient(135deg, #0B1929 0%, #0E2B45 45%, #153D60 100%)",
        border: "1px solid rgba(55,138,221,0.2)",
        color: "#F8FBFF",
        boxShadow: "0 8px 32px rgba(11,25,41,0.3), inset 0 1px 0 rgba(255,255,255,0.06)",
        position: "relative",
        overflow: "hidden",
      }}>
        {/* Background orbs */}
        <div style={{ position: "absolute", right: -40, top: -40, width: 200, height: 200, borderRadius: "50%", background: "radial-gradient(circle, rgba(55,138,221,0.15), transparent 70%)", pointerEvents: "none" }} />
        <div style={{ position: "absolute", right: 100, bottom: -30, width: 150, height: 150, borderRadius: "50%", background: "radial-gradient(circle, rgba(29,158,117,0.12), transparent 70%)", pointerEvents: "none" }} />

        <div style={{ position: "relative", display: "flex", justifyContent: "space-between", alignItems: "flex-start", flexWrap: "wrap", gap: 12 }}>
          <div>
            <div style={{ fontSize: 10, textTransform: "uppercase", letterSpacing: "1.5px", color: "rgba(255,255,255,0.5)", marginBottom: 6 }}>
              Territory Atlas
            </div>
            <div style={{ fontSize: 22, fontWeight: 700, letterSpacing: "-0.4px" }}>
              {selectedMeta?.name || perfData?.territory_name || "Command Center"}
            </div>
            <div style={{ fontSize: 12, color: "rgba(255,255,255,0.65)", marginTop: 4 }}>
              {activeCompany || "No company"} · {period || "All time"}
              {selectedMeta && <span style={{ marginLeft: 8, color: selColor, fontWeight: 500 }}>
                {selectedMeta.region} · {selectedMeta.segment}
              </span>}
            </div>
          </div>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
            {[
              { label: "Total", value: allTerritories.length, color: "#A8CFF4" },
              { label: "Shown", value: filteredTerritories.length, color: "#8EE7C6" },
              { label: "Reps", value: perfData?.assigned_reps ?? 0, color: "#FFD59D" },
            ].map(b => (
              <div key={b.label} style={{
                background: "rgba(255,255,255,0.07)",
                border: "1px solid rgba(255,255,255,0.12)",
                borderRadius: 10, padding: "7px 14px",
                backdropFilter: "blur(8px)",
                textAlign: "center", minWidth: 60,
              }}>
                <div style={{ fontSize: 18, fontWeight: 700, color: b.color, lineHeight: 1 }}>{b.value}</div>
                <div style={{ fontSize: 9, textTransform: "uppercase", letterSpacing: "0.6px", color: "rgba(255,255,255,0.5)", marginTop: 3 }}>{b.label}</div>
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* ── Two-column layout ────────────────────────────────────────────── */}
      <div style={{ display: "grid", gridTemplateColumns: "280px 1fr", gap: 16, alignItems: "start" }}>

        {/* ── LEFT: Directory ─────────────────────────────────────────────── */}
        <div style={{
          borderRadius: 14,
          border: "1px solid var(--color-border-secondary)",
          background: "var(--color-background-primary)",
          overflow: "hidden",
          boxShadow: "0 4px 16px rgba(0,0,0,0.06)",
        }}>
          <div style={{
            padding: "12px 14px",
            borderBottom: "1px solid var(--color-border-secondary)",
            background: "var(--color-background-secondary)",
          }}>
            <div style={{ fontSize: 12, fontWeight: 700, marginBottom: 8 }}>Territory Directory</div>
            <input
              value={search}
              onChange={e => setSearch(e.target.value)}
              placeholder="Search territory, region…"
              style={{
                width: "100%", padding: "7px 10px", borderRadius: 8,
                border: "1px solid var(--color-border-primary)",
                fontSize: 12, background: "var(--color-background-primary)",
                color: "var(--color-text-primary)", boxSizing: "border-box",
                outline: "none",
              }}
            />
            {regionMix.length > 0 && (
              <div style={{ display: "flex", flexWrap: "wrap", gap: 4, marginTop: 8 }}>
                {regionMix.map(({ label, color, count }) => (
                  <span key={label} style={{
                    fontSize: 10, borderRadius: 6, padding: "2px 7px",
                    background: `${color}18`, color, fontWeight: 500,
                    border: `1px solid ${color}30`,
                  }}>
                    {label} {count}
                  </span>
                ))}
              </div>
            )}
          </div>

          <div style={{ maxHeight: 560, overflowY: "auto", padding: "8px 10px", display: "grid", gap: 4 }}>
            {loading ? (
              [1,2,3,4].map(i => <Skeleton key={i} h={54} r={10} />)
            ) : filteredTerritories.length > 0 ? (
              filteredTerritories.map(t => (
                <TerritoryCard
                  key={t.id}
                  territory={t}
                  isSelected={selectedTerritory === t.id}
                  onClick={() => { setSelectedTerritory(t.id); setRepPage(1); setActiveSection("overview"); }}
                />
              ))
            ) : (
              <div style={{ padding: 12, fontSize: 12, color: "var(--color-text-secondary)", textAlign: "center" }}>
                No territories found
              </div>
            )}
            {territoriesError && (
              <div style={{ padding: 8, fontSize: 11, color: "#D85A30" }}>{territoriesError}</div>
            )}
          </div>
        </div>

        {/* ── RIGHT: Detail panel ─────────────────────────────────────────── */}
        <div>
          {!selectedTerritory ? (
            <div style={{
              borderRadius: 14, padding: 48, textAlign: "center",
              border: "1px solid var(--color-border-secondary)",
              background: "var(--color-background-primary)",
              color: "var(--color-text-secondary)", fontSize: 13,
            }}>
              <div style={{ fontSize: 32, marginBottom: 12 }}>🗺️</div>
              Select a territory from the directory to explore performance insights.
            </div>
          ) : perfLoading ? (
            <div style={{ display: "grid", gap: 12 }}>
              <Skeleton h={240} /><Skeleton h={110} /><Skeleton h={200} />
            </div>
          ) : !perfData ? (
            <div style={{ borderRadius: 14, padding: 24, border: "1px solid var(--color-border-secondary)", background: "var(--color-background-primary)", color: "var(--color-text-secondary)", fontSize: 13 }}>
              No performance data available for this territory.
            </div>
          ) : (
            <div className="territory-panel" style={{ display: "grid", gap: 14 }}>

              {/* ── Interactive world map ─────────────────────────────────── */}
              <div style={{
                borderRadius: 14,
                overflow: "hidden",
                border: "1px solid rgba(55,138,221,0.2)",
                boxShadow: "0 4px 20px rgba(11,25,41,0.12)",
              }}>
                <WorldMap
                  points={mapPoints}
                  selectedId={selectedTerritory}
                  onSelect={(id) => { setSelectedTerritory(id); setRepPage(1); setActiveSection("overview"); }}
                />
                {/* Legend strip */}
                <div style={{
                  display: "flex", flexWrap: "wrap", gap: 12, padding: "8px 14px",
                  background: "rgba(11,25,41,0.92)", borderTop: "1px solid rgba(255,255,255,0.08)",
                }}>
                  {(() => {
                    const seen = new Map();
                    mapPoints.forEach(pt => {
                      const key = String(pt.region || "").toUpperCase().trim();
                      const anchor = pt._anchor || getAnchor(pt.region);
                      if (!seen.has(key)) seen.set(key, { label: anchor.label, color: anchor.color, count: 0 });
                      seen.get(key).count += 1;
                    });
                    return Array.from(seen.values()).map(({ label, color, count }) => (
                      <div key={label} style={{ display: "flex", alignItems: "center", gap: 5 }}>
                        <div style={{ width: 8, height: 8, borderRadius: "50%", background: color }} />
                        <span style={{ fontSize: 10, color: "rgba(255,255,255,0.65)" }}>{label}</span>
                        <span style={{ fontSize: 10, color, fontWeight: 600 }}>{count}</span>
                      </div>
                    ));
                  })()}
                  <div style={{ marginLeft: "auto", fontSize: 10, color: "rgba(255,255,255,0.4)" }}>
                    {filteredTerritories.length} territories · click to select
                  </div>
                </div>
              </div>

              {/* ── Warnings ─────────────────────────────────────────────── */}
              {(criticalWarnings.length > 0 || fallbackWarning) && (
                <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
                  {criticalWarnings.slice(0, 4).map((w, i) => (
                    <span key={i} style={{
                      fontSize: 11, borderRadius: 999, padding: "4px 10px",
                      background: "rgba(216,90,48,0.12)", color: "#D85A30",
                      border: "1px solid rgba(216,90,48,0.2)",
                    }}>⚠ {w}</span>
                  ))}
                  {fallbackWarning && (
                    <span style={{
                      fontSize: 11, borderRadius: 999, padding: "4px 10px",
                      background: "var(--color-background-secondary)", color: "var(--color-text-secondary)",
                      border: "1px solid var(--color-border-secondary)",
                    }}>ℹ {fallbackWarning}</span>
                  )}
                </div>
              )}

              {/* ── Tabs ─────────────────────────────────────────────────── */}
              <div style={{
                display: "flex", gap: 4, padding: 4,
                background: "var(--color-background-secondary)",
                borderRadius: 10, border: "1px solid var(--color-border-secondary)",
                width: "fit-content",
              }}>
                {TABS.map(tab => (
                  <button key={tab} onClick={() => setActiveSection(tab)} style={{
                    padding: "6px 14px", borderRadius: 7, fontSize: 12, fontWeight: 500,
                    border: "none", cursor: "pointer", fontFamily: "var(--font-sans)",
                    background: activeSection === tab ? "var(--color-background-primary)" : "transparent",
                    color: activeSection === tab ? "var(--color-text-primary)" : "var(--color-text-secondary)",
                    boxShadow: activeSection === tab ? "0 2px 8px rgba(0,0,0,0.08)" : "none",
                    transition: "all 0.15s ease",
                  }}>
                    {TAB_LABELS[tab]}
                  </button>
                ))}
              </div>

              {/* ── OVERVIEW TAB ─────────────────────────────────────────── */}
              {activeSection === "overview" && (
                <div style={{ display: "grid", gap: 14 }}>

                  {/* KPI row */}
                  <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(160px, 1fr))", gap: 12 }}>
                    <KpiCard label="Revenue" value={fmt(perfData.revenue || 0)} sub="Realized in period" accent="#1D9E75" icon="💰" trend="up" />
                    <KpiCard label="Deals Won" value={String(perfData.deals_won || 0)} sub="Closed-won" accent="#378ADD" icon="🏆" />
                    <KpiCard label="Win Rate" value={pct(perfData.win_rate || 0)} sub="Won / total closed" accent={Number(perfData.win_rate||0) >= 30 ? "#1D9E75" : "#EF9F27"} icon="🎯" trend={Number(perfData.win_rate||0) >= 30 ? "up" : "down"} />
                    <KpiCard label="Open Pipeline" value={fmt(perfData.open_pipeline || 0)} sub={`${perfData.assigned_reps||0} reps assigned`} accent="#8B5CF6" icon="📈" />
                  </div>

                  {/* Brief + Top performers side-by-side */}
                  <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
                    {/* Brief card */}
                    <div style={{
                      borderRadius: 14, padding: "16px 18px",
                      border: `1px solid ${selColor}33`,
                      background: `linear-gradient(140deg, ${selColor}0a, var(--color-background-primary))`,
                    }}>
                      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 10 }}>
                        <div style={{ width: 36, height: 36, borderRadius: 10, background: `${selColor}22`, border: `1px solid ${selColor}44`, display: "flex", alignItems: "center", justifyContent: "center", fontSize: 18 }}>🗺️</div>
                        <div>
                          <div style={{ fontSize: 14, fontWeight: 700 }}>{perfData.territory_name || selectedMeta?.name}</div>
                          <div style={{ fontSize: 11, color: selColor, fontWeight: 500 }}>
                            {perfData.region || selectedMeta?.region} · {perfData.segment || selectedMeta?.segment}
                          </div>
                        </div>
                      </div>
                      <div style={{ fontSize: 12, lineHeight: 1.7, color: "var(--color-text-secondary)" }}>
                        {territoryNarrative(perfData)}
                      </div>
                    </div>

                    {/* Top performers */}
                    <div style={{
                      borderRadius: 14, padding: "16px 18px",
                      border: "1px solid var(--color-border-secondary)",
                      background: "var(--color-background-primary)",
                    }}>
                      <div style={{ fontSize: 13, fontWeight: 700, marginBottom: 12 }}>🏅 Top Performers</div>
                      {topPerformers.length > 0 ? (
                        <div style={{ display: "grid", gap: 8 }}>
                          {topPerformers.map((rep, idx) => {
                            const medals = ["🥇", "🥈", "🥉"];
                            const att = Number(rep.attainment_pct || 0);
                            const color = repAttainmentColor(att);
                            return (
                              <div key={rep.rep_id || rep.name} style={{
                                display: "flex", justifyContent: "space-between", alignItems: "center",
                                padding: "8px 10px", borderRadius: 10,
                                background: "var(--color-background-secondary)",
                                border: "1px solid var(--color-border-secondary)",
                              }}>
                                <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                                  <span style={{ fontSize: 16 }}>{medals[idx]}</span>
                                  <div>
                                    <div style={{ fontSize: 12, fontWeight: 600 }}>{rep.name || "Rep"}</div>
                                    <div style={{ fontSize: 10, color: "var(--color-text-tertiary)" }}>{rep.deals_won ?? 0} won</div>
                                  </div>
                                </div>
                                <div style={{ textAlign: "right" }}>
                                  <div style={{ fontSize: 12, fontWeight: 700 }}>{fmt(rep.revenue || 0)}</div>
                                  <div style={{
                                    fontSize: 10, fontWeight: 600, color,
                                    background: `${color}18`, borderRadius: 4, padding: "1px 5px",
                                  }}>{pct(att)}</div>
                                </div>
                              </div>
                            );
                          })}
                        </div>
                      ) : (
                        <div style={{ fontSize: 12, color: "var(--color-text-secondary)" }}>No rep data available.</div>
                      )}
                    </div>
                  </div>

                  {/* Revenue distribution chart */}
                  {repChartData.length > 0 && (
                    <div style={{
                      borderRadius: 14, padding: "16px 18px",
                      border: "1px solid var(--color-border-secondary)",
                      background: "var(--color-background-primary)",
                    }}>
                      <div style={{ fontSize: 13, fontWeight: 700, marginBottom: 4 }}>Rep Revenue Distribution</div>
                      <div style={{ fontSize: 11, color: "var(--color-text-secondary)", marginBottom: 14 }}>Top {repChartData.length} reps by revenue</div>
                      <div style={{ width: "100%", height: 200 }}>
                        <ResponsiveContainer>
                          <BarChart data={repChartData} margin={{ left: 4, right: 8, top: 4, bottom: 0 }}>
                            <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border-tertiary)" vertical={false} />
                            <XAxis dataKey="name" tick={{ fontSize: 11, fill: "var(--color-text-secondary)" }} axisLine={false} tickLine={false} />
                            <YAxis tick={{ fontSize: 10, fill: "var(--color-text-secondary)" }} tickFormatter={v => fmt(v)} axisLine={false} tickLine={false} />
                            <Tooltip content={<CustomBarTooltip />} cursor={{ fill: "rgba(55,138,221,0.07)" }} />
                            <Bar dataKey="revenue" radius={[6, 6, 0, 0]} maxBarSize={40}>
                              {repChartData.map((entry, idx) => (
                                <Cell
                                  key={idx}
                                  fill={`hsl(${210 + idx * 12}, 70%, ${50 + idx * 3}%)`}
                                />
                              ))}
                            </Bar>
                          </BarChart>
                        </ResponsiveContainer>
                      </div>
                    </div>
                  )}
                </div>
              )}

              {/* ── REPS TAB ─────────────────────────────────────────────── */}
              {activeSection === "reps" && (
                <div style={{
                  borderRadius: 14, overflow: "hidden",
                  border: "1px solid var(--color-border-secondary)",
                  background: "var(--color-background-primary)",
                }}>
                  <div style={{
                    display: "flex", justifyContent: "space-between", alignItems: "center",
                    padding: "14px 18px", borderBottom: "1px solid var(--color-border-secondary)",
                    background: "var(--color-background-secondary)",
                  }}>
                    <div style={{ fontSize: 13, fontWeight: 700 }}>Rep Coverage</div>
                    <span style={{
                      fontSize: 11, padding: "3px 10px", borderRadius: 999,
                      background: `${selColor}18`, color: selColor, fontWeight: 500,
                    }}>{reps.length} reps</span>
                  </div>
                  {reps.length > 0 ? (
                    <>
                      <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
                        <thead>
                          <tr style={{ background: "var(--color-background-secondary)" }}>
                            {["Rep", "Revenue", "Deals Won", "Attainment"].map((h, i) => (
                              <th key={h} style={{
                                padding: "10px 10px", fontWeight: 600, fontSize: 11,
                                color: "var(--color-text-secondary)", textTransform: "uppercase",
                                letterSpacing: "0.5px", textAlign: i === 0 ? "left" : "right",
                                borderBottom: "1px solid var(--color-border-secondary)",
                              }}>{h}</th>
                            ))}
                          </tr>
                        </thead>
                        <tbody>
                          {pagedReps.map((rep, idx) => (
                            <RepRow key={rep.rep_id || rep.user_id || rep.name} rep={rep} rank={(safeRepPage - 1) * repPageSize + idx + 1} />
                          ))}
                        </tbody>
                      </table>
                      <div style={{ padding: "4px 8px", borderTop: "1px solid var(--color-border-secondary)" }}>
                        <PaginationControls
                          page={safeRepPage}
                          pageSize={repPageSize}
                          totalItems={reps.length}
                          onPageChange={setRepPage}
                          onPageSizeChange={(n) => { setRepPageSize(n); setRepPage(1); }}
                        />
                      </div>
                    </>
                  ) : (
                    <div style={{ padding: 28, textAlign: "center", fontSize: 12, color: "var(--color-text-secondary)" }}>
                      No reps assigned to this territory.
                    </div>
                  )}
                </div>
              )}

              {/* ── HYGIENE TAB ──────────────────────────────────────────── */}
              {activeSection === "hygiene" && (
                <div style={{ display: "grid", gap: 12 }}>
                  <div style={{
                    borderRadius: 14, padding: "14px 18px",
                    border: "1px solid var(--color-border-secondary)",
                    background: "var(--color-background-secondary)",
                  }}>
                    <div style={{ fontSize: 13, fontWeight: 700, marginBottom: 4 }}>Pipeline Hygiene</div>
                    <div style={{ fontSize: 12, color: "var(--color-text-secondary)" }}>
                      Flags that require immediate RevOps attention. Lower is better.
                    </div>
                  </div>
                  {perfData.pipeline_hygiene ? (
                    <div style={{ display: "grid", gap: 10 }}>
                      {hygieneRows.map(({ key, label, color, icon }) => (
                        <HygieneBar
                          key={key}
                          label={label}
                          icon={icon}
                          value={perfData.pipeline_hygiene[key]}
                          color={color}
                          maxValue={maxHygiene}
                        />
                      ))}
                    </div>
                  ) : (
                    <div style={{ padding: 20, textAlign: "center", fontSize: 12, color: "var(--color-text-secondary)", borderRadius: 14, border: "1px solid var(--color-border-secondary)", background: "var(--color-background-primary)" }}>
                      No hygiene data available for this territory.
                    </div>
                  )}

                  {/* Hygiene score summary */}
                  {perfData.pipeline_hygiene && (() => {
                    const total = hygieneRows.reduce((s, r) => s + Number(perfData.pipeline_hygiene[r.key] || 0), 0);
                    const severity = total === 0 ? "Clean" : total <= 3 ? "Needs attention" : "Critical";
                    const sevColor = total === 0 ? "#1D9E75" : total <= 3 ? "#EF9F27" : "#D85A30";
                    return (
                      <div style={{
                        borderRadius: 14, padding: "14px 18px",
                        border: `1px solid ${sevColor}33`,
                        background: `${sevColor}08`,
                        display: "flex", justifyContent: "space-between", alignItems: "center",
                      }}>
                        <div>
                          <div style={{ fontSize: 12, fontWeight: 700 }}>Overall Hygiene Status</div>
                          <div style={{ fontSize: 11, color: "var(--color-text-secondary)", marginTop: 2 }}>
                            {total} total flags across all categories
                          </div>
                        </div>
                        <div style={{
                          fontSize: 13, fontWeight: 700, color: sevColor,
                          background: `${sevColor}18`, borderRadius: 8, padding: "5px 14px",
                          border: `1px solid ${sevColor}33`,
                        }}>{severity}</div>
                      </div>
                    );
                  })()}
                </div>
              )}

            </div>
          )}
        </div>
      </div>
    </div>
  );
}
