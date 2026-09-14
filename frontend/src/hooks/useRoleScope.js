import { useEffect, useMemo, useState } from "react";
import { useFetch } from "./useFetch";
import { withRefresh } from "../utils/format";

/**
 * useRoleScope — shared "who am I looking at" state for role-scoped pages
 * outside My Performance / Team Command Center, which already carry their
 * own copies of this exact picker. Centralizes the org-structure team-
 * fragmentation fix (a team's roster is split across territory buckets --
 * see TeamCommandCenterPage) so a third copy doesn't silently regress it.
 *
 * sales_rep gets a rep picker (defaults to top revenue); sales_manager gets
 * a team picker (defaults to top revenue team) plus that team's roster as
 * rep_ids. Every other role is unscoped: scopeQuery is {} and nothing here
 * fetches.
 */
export function useRoleScope({ role, company, refreshKey }) {
  const isRepScoped = role === "sales_rep";
  const isTeamScoped = role === "sales_manager";

  const [selectedRepId, setSelectedRepId] = useState(null);
  const [selectedTeamId, setSelectedTeamId] = useState(null);

  const { data: repsData, loading: repsLoading } = useFetch(
    isRepScoped ? withRefresh("/analytics/reps/performance", refreshKey) : null,
    { role, company }
  );
  const sortedReps = useMemo(
    () => [...(repsData || [])].sort((a, b) => Number(b.revenue || 0) - Number(a.revenue || 0)),
    [repsData]
  );

  const { data: orgData, loading: orgLoading } = useFetch(
    isTeamScoped ? withRefresh("/analytics/org-structure", refreshKey) : null,
    { role, company }
  );
  const teams = useMemo(() => {
    const territories = orgData?.territories || [];
    const byId = new Map();
    for (const t of territories) {
      for (const team of t.teams || []) {
        const existing = byId.get(team.team_id);
        if (existing) {
          const seen = new Set(existing.members.map((m) => m.rep_id));
          for (const m of team.members || []) {
            if (!seen.has(m.rep_id)) {
              seen.add(m.rep_id);
              existing.members.push(m);
            }
          }
        } else {
          byId.set(team.team_id, {
            team_id: team.team_id,
            team_name: team.team_name,
            region: team.region,
            members: [...(team.members || [])],
          });
        }
      }
    }
    return [...byId.values()].sort(
      (a, b) => b.members.reduce((s, m) => s + Number(m.revenue || 0), 0) - a.members.reduce((s, m) => s + Number(m.revenue || 0), 0)
    );
  }, [orgData]);

  useEffect(() => {
    setSelectedRepId(null);
    setSelectedTeamId(null);
  }, [role, company, refreshKey]);
  useEffect(() => {
    if (isRepScoped && !selectedRepId && sortedReps.length) setSelectedRepId(sortedReps[0].rep_id);
  }, [isRepScoped, selectedRepId, sortedReps]);
  useEffect(() => {
    if (isTeamScoped && !selectedTeamId && teams.length) setSelectedTeamId(teams[0].team_id);
  }, [isTeamScoped, selectedTeamId, teams]);

  const selectedTeam = useMemo(() => teams.find((t) => t.team_id === selectedTeamId) || null, [teams, selectedTeamId]);
  const rosterIds = useMemo(() => new Set((selectedTeam?.members || []).map((m) => m.rep_id)), [selectedTeam]);
  const selectedRep = useMemo(() => sortedReps.find((r) => r.rep_id === selectedRepId) || null, [sortedReps, selectedRepId]);

  // null (not {}) while a scoped role's picker hasn't resolved a selection
  // yet, so callers can gate a fetch on it instead of firing one unscoped
  // request in the gap between mount and the picker's default-select effect.
  const scopeQuery = isRepScoped
    ? (selectedRepId ? { rep_id: selectedRepId } : null)
    : isTeamScoped
      ? (selectedTeamId ? { team_id: selectedTeamId } : null)
      : {};

  const scopeLabel = isRepScoped
    ? selectedRep?.name || null
    : isTeamScoped
      ? (selectedTeam ? `${selectedTeam.team_name} (${rosterIds.size} rep${rosterIds.size === 1 ? "" : "s"})` : null)
      : null;

  return {
    isRepScoped,
    isTeamScoped,
    isScoped: isRepScoped || isTeamScoped,
    loading: (isRepScoped && repsLoading) || (isTeamScoped && orgLoading),
    selectedRepId,
    setSelectedRepId,
    sortedReps,
    selectedTeamId,
    setSelectedTeamId,
    teams,
    selectedTeam,
    rosterIds,
    scopeQuery,
    scopeLabel,
  };
}
