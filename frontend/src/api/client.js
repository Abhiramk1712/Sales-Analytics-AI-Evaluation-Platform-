/** Shared API client — single source for all backend requests */

const API_BASE_URL = import.meta.env.VITE_API_URL || "";

/**
 * Ambient request context: who is asking, and for which company.
 *
 * Every tenant-scoped request needs these two headers. Passing them at each of
 * the ~60 call sites is the same fragility as scoping database queries by hand —
 * 20 of them already omitted the company, so those views silently showed
 * whichever tenant the server defaults to while the selector said otherwise.
 * They are supplied once here and overridden per call only when a caller
 * genuinely needs a different value.
 */
let requestContext = { role: "", company: "" };

export function setRequestContext({ role, company } = {}) {
  requestContext = {
    role: role || "",
    company: company || "",
  };
}

export function getRequestContext() {
  return requestContext;
}

/** Per-call options win; the ambient context fills the gaps. */
export function resolveContext(opts = {}) {
  return {
    role: opts.role || requestContext.role,
    company: opts.company || requestContext.company,
  };
}

function safeJson(res) {
  return res.json().catch(() => null);
}

/**
 * GET request to the backend API.
 * @param {string} path - API path (e.g. "/analytics/kpis")
 * @param {object} opts - { role, company, params }
 */
export async function apiGet(path, opts = {}) {
  const { params, signal } = opts;
  const { role, company } = resolveContext(opts);
  const url = new URL(`${API_BASE_URL}${path}`, window.location.origin);
  Object.entries(params || {}).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== "") {
      url.searchParams.set(key, value);
    }
  });

  const res = await fetch(url, {
    signal,
    headers: {
      "Content-Type": "application/json",
      ...(role ? { "X-User-Role": role } : {}),
      ...(company ? { "X-Company-Id": company } : {}),
    },
  });

  if (!res.ok) {
    const body = await safeJson(res);
    throw new Error(body?.detail || `Request failed: ${res.status}`);
  }

  return res.json();
}

/**
 * POST request to the backend API.
 * @param {string} path - API path
 * @param {object} data - JSON body
 * @param {object} opts - { role, company }
 */
export async function apiPost(path, data, opts = {}) {
  const { role, company } = resolveContext(opts);
  const url = `${API_BASE_URL}${path}`;
  const res = await fetch(url, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(role ? { "X-User-Role": role } : {}),
      ...(company ? { "X-Company-Id": company } : {}),
    },
    body: JSON.stringify(data),
  });

  if (!res.ok) {
    const body = await safeJson(res);
    throw new Error(body?.detail || `Request failed: ${res.status}`);
  }

  return res.json();
}

/**
 * POST with FormData (for file uploads).
 */
export async function apiPostForm(path, formData, opts = {}) {
  const { role, company } = resolveContext(opts);
  const url = `${API_BASE_URL}${path}`;
  const res = await fetch(url, {
    method: "POST",
    headers: {
      ...(role ? { "X-User-Role": role } : {}),
      ...(company ? { "X-Company-Id": company } : {}),
    },
    body: formData,
  });

  if (!res.ok) {
    const body = await safeJson(res);
    throw new Error(body?.detail || `Request failed: ${res.status}`);
  }

  return res.json();
}
