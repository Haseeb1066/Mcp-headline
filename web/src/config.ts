/** API origin for fetch calls. Empty string = same origin (production or Vite /api proxy). */
export function apiBase(): string {
  const raw = import.meta.env.VITE_API_BASE;
  if (typeof raw === "string" && raw.trim()) {
    return raw.replace(/\/$/, "");
  }
  return "";
}

export function apiUrl(path: string): string {
  const base = apiBase();
  const p = path.startsWith("/") ? path : `/${path}`;
  return base ? `${base}${p}` : p;
}

function isInIframe(): boolean {
  if (typeof window === "undefined") return false;
  try {
    return window.self !== window.top;
  } catch {
    return true;
  }
}

/** True when running inside Tableau (iframe or Extensions API). */
export function isTableauHost(): boolean {
  if (typeof window === "undefined") return false;
  if (window.tableau?.extensions) return true;
  return isInIframe();
}

export function queryParam(name: string): string | null {
  if (typeof window === "undefined") return null;
  const v = new URLSearchParams(window.location.search).get(name);
  return v?.trim() || null;
}

/** Live server test via ?contentUrl=... (PAT). Disabled inside Tableau extension. */
export function useServerContentMode(): boolean {
  if (typeof window !== "undefined" && window.tableau?.extensions) {
    return false;
  }
  return Boolean(queryParam("contentUrl") || queryParam("datasource") || queryParam("workbookId"));
}

export function useMockMode(): boolean {
  if (typeof window === "undefined") return false;
  const params = new URLSearchParams(window.location.search);
  // Mock only when explicitly requested — never default to fake data
  return params.get("mock") === "1";
}
