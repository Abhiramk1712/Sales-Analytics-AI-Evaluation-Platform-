import { useState, useEffect } from "react";
import { API } from "../utils/format";

export function useFetch(url) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (!url) return;
    const controller = new AbortController();

    setLoading(true);
    setData(null);
    setError(null);

    fetch(API + url, { signal: controller.signal })
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
  }, [url]);

  return { data, loading, error };
}
