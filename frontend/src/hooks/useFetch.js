import { useState, useEffect } from "react";
import { apiGet } from "../api/client";

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

    apiGet(url, { role, company, signal: controller.signal })
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
