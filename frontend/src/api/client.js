/** Shared API client — single source for all backend requests */

const API_BASE_URL = import.meta.env.VITE_API_URL || "";

function safeJson(res) {
  return res.json().catch(() => null);
}

/**
 * GET request to the backend API.
 * @param {string} path - API path (e.g. "/analytics/kpis")
 * @param {object} opts - { role, company, params }
 */
export async function apiGet(path, { role, company, params, signal } = {}) {
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
export async function apiPost(path, data, { role, company } = {}) {
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
export async function apiPostForm(path, formData, { role, company } = {}) {
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
