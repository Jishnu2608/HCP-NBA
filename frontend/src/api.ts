// Thin fetch wrapper. The token lives in sessionStorage so each browser tab can hold a
// different persona, which is how the demo shows two roles side by side.

// API payloads are plain JSON shaped by the backend serializers.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
export type Json = any;

const TOKEN_KEY = "nba_token";

export class ApiError extends Error {
  status: number;
  detail: unknown;
  constructor(status: number, detail: unknown) {
    super(describe(detail));
    this.status = status;
    this.detail = detail;
  }
}

function describe(detail: unknown): string {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail.map((d) => (typeof d === "string" ? d : (d?.msg ?? JSON.stringify(d)))).join("; ");
  }
  return "Request failed";
}

export const getToken = () => sessionStorage.getItem(TOKEN_KEY);
export const setToken = (token: string | null) =>
  token ? sessionStorage.setItem(TOKEN_KEY, token) : sessionStorage.removeItem(TOKEN_KEY);

export async function api<T = Json>(
  path: string,
  options: { method?: string; body?: unknown; form?: Record<string, string> } = {},
): Promise<T> {
  const headers: Record<string, string> = {};
  const token = getToken();
  if (token) headers.Authorization = `Bearer ${token}`;
  let body: BodyInit | undefined;
  if (options.form) {
    headers["Content-Type"] = "application/x-www-form-urlencoded";
    body = new URLSearchParams(options.form).toString();
  } else if (options.body !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(options.body);
  }
  const response = await fetch(`/api${path}`, { method: options.method ?? "GET", headers, body });
  if (response.status === 401 && token) {
    setToken(null);
    window.location.assign("/login");
  }
  const payload = response.headers.get("content-type")?.includes("json")
    ? await response.json()
    : null;
  if (!response.ok) throw new ApiError(response.status, payload?.detail ?? response.statusText);
  return payload as T;
}

export const post = <T = Json>(path: string, body: unknown = {}) =>
  api<T>(path, { method: "POST", body });
export const put = <T = Json>(path: string, body: unknown) => api<T>(path, { method: "PUT", body });
export const patch = <T = Json>(path: string, body: unknown) =>
  api<T>(path, { method: "PATCH", body });

export function query(params: Record<string, string | number | undefined | null | string[]>) {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === "") continue;
    if (Array.isArray(value)) value.forEach((v) => search.append(key, v));
    else search.set(key, String(value));
  }
  const text = search.toString();
  return text ? `?${text}` : "";
}
