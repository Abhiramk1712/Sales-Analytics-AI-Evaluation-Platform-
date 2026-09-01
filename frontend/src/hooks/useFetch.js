import { useState, useEffect } from "react";
import { API } from "../utils/format";

/**
 * useFetch — data fetching hook with role/company header support.
 * @param {string} url - API path (relative)
 * @param {object} opts - { role, company } — attached as X-User-Role / X-Company-Id headers
 */
export function useFetch(url, opts = {}) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const role = opts?.role || "";
  const company = opts?.company || "";

  useEffect(() => {
    if (!url) return;
    const controller = new AbortController();

    setLoading(true);
    setData(null);
    setError(null);

    const headers = { "Content-Type": "application/json" };
    if (role) headers["X-User-Role"] = role;
    if (company) headers["X-Company-Id"] = company;

    fetch(API + url, { signal: controller.signal, headers })
      .then(async (r) => {
        const body = await r.json().catch(() => ({}));
        if (!r.ok) {
          const detail =
            typeof body?.detail === "string"
              ? body.detail
              : `Request failed (${r.status})`;
          throw new Error(detail);
        }
        return body;
      })
      .then((d) => {
        if (!controller.signal.aborted) {
          setData(d);
          setLoading(false);
        }
      })
      .catch((e) => {
        if (controller.signal.aborted) return;
        setError(e.message);
        setLoading(false);
      });

    return () => {
      controller.abort();
    };
  }, [url, role, company]);

  return { data, loading, error };
}
