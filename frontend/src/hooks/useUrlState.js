import { useCallback, useEffect, useRef, useState } from "react";

/**
 * Keep a small set of view parameters in the URL query string.
 *
 * Navigation used to live entirely in component state, so every view in the
 * application shared one address. That cost more than tidiness: a rep scorecard
 * or a payout period could not be linked or shared, the back button left the app
 * instead of undoing a tab change, a reload reset everything, and any tooling —
 * screenshots, E2E tests — had to click its way to a view rather than visit it.
 *
 * **Why not react-router.** A router wants the view tree expressed as routes,
 * and this app renders tabs as `{tab === "X" && <XTab/>}` inside a 3,400-line
 * component with no frontend tests to catch a bad restructure. The sub-views
 * (payout view, scorecard tab, selected rep) would need query parameters even
 * with a router in place. Syncing the same handful of values to the query string
 * delivers the addressability without touching the component tree.
 *
 * Values equal to their default are omitted, so a default view stays at `/`
 * rather than accumulating noise.
 *
 *     const [view, setView] = useUrlState({ tab: "Dashboard", company: "" });
 *     setView({ tab: "Payouts" });          // pushes ?tab=Payouts
 *     setView({ period: "Q2 2026" }, { replace: true });  // no history entry
 */
export function useUrlState(defaults) {
  // The default set is fixed for the lifetime of the hook; keeping it in a ref
  // means callers can pass an object literal without causing a re-render loop.
  const defaultsRef = useRef(defaults);

  const read = useCallback(() => {
    const params = new URLSearchParams(window.location.search);
    const next = {};
    for (const [key, fallback] of Object.entries(defaultsRef.current)) {
      const value = params.get(key);
      next[key] = value === null || value === "" ? fallback : value;
    }
    return next;
  }, []);

  const [state, setState] = useState(read);

  const write = useCallback((next, { replace = false } = {}) => {
    const params = new URLSearchParams(window.location.search);
    for (const [key, fallback] of Object.entries(defaultsRef.current)) {
      const value = next[key];
      if (value === undefined || value === null || value === "" || value === fallback) {
        params.delete(key);
      } else {
        params.set(key, value);
      }
    }
    const query = params.toString();
    const url = `${window.location.pathname}${query ? `?${query}` : ""}`;
    if (url === `${window.location.pathname}${window.location.search}`) return;
    if (replace) {
      window.history.replaceState({}, "", url);
    } else {
      window.history.pushState({}, "", url);
    }
  }, []);

  const setView = useCallback((patch, options) => {
    setState((current) => {
      const next = { ...current, ...patch };
      write(next, options);
      return next;
    });
  }, [write]);

  // Back and forward re-read the URL rather than pushing another entry.
  useEffect(() => {
    const onPop = () => setState(read());
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, [read]);

  return [state, setView];
}
