// Thin fetch wrapper. Turns API errors into ApiError.
//
// The session lives in an HttpOnly cookie that this code cannot read; the browser sends it
// with every same-origin request. For state-changing requests the server also requires the
// CSRF token from the readable `nba_csrf` cookie to be echoed in a header, which another
// website cannot do.
const SAFE = new Set(["GET", "HEAD", "OPTIONS"]);

function csrfToken(): string | null {
  const match = document.cookie.match(/(?:^|;\s*)nba_csrf=([^;]+)/);
  return match ? decodeURIComponent(match[1]) : null;
}

// API payloads are plain JSON shaped by the backend serializers.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
export type Json = any;

export class ApiError extends Error {
  status: number;
  /** Stable machine-readable code from the API, when it sent one. */
  code: string | null;
  detail: Json;
  constructor(status: number, detail: unknown) {
    super(describe(detail));
    this.status = status;
    this.detail = detail;
    this.code = typeof detail === "object" && detail !== null && "code" in detail ? String((detail as Json).code) : null;
  }
}

function describe(detail: unknown): string {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail.map((d) => (typeof d === "string" ? d : (d?.msg ?? JSON.stringify(d)))).join("; ");
  }
  if (typeof detail === "object" && detail !== null && "message" in detail) {
    return String((detail as Json).message);
  }
  return "Request failed";
}

// Called when the server says the session is no longer valid (expired, signed out
// elsewhere, account disabled). Set by the auth provider.
let onSessionLost: () => void = () => {};
export const setSessionLostHandler = (handler: () => void) => {
  onSessionLost = handler;
};

export async function api<T = Json>(
  path: string,
  options: { method?: string; body?: unknown } = {},
): Promise<T> {
  const method = (options.method ?? "GET").toUpperCase();
  const headers: Record<string, string> = {};
  if (!SAFE.has(method)) {
    // No token yet in a brand-new browser: one harmless read makes the server issue it.
    if (!csrfToken()) await fetch("/api/health", { credentials: "same-origin" }).catch(() => undefined);
    const token = csrfToken();
    if (token) headers["X-CSRF-Token"] = token;
  }
  let body: BodyInit | undefined;
  if (options.body !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(options.body);
  }
  let response: Response;
  try {
    response = await fetch(`/api${path}`, { method, headers, body, credentials: "same-origin" });
  } catch {
    // Offline, server down, connection reset: status 0 marks a network failure.
    throw new ApiError(0, { code: "network", message: "The server could not be reached." });
  }
  const payload = response.headers.get("content-type")?.includes("json")
    ? await response.json()
    : null;
  if (!response.ok) {
    const error = new ApiError(response.status, payload?.detail ?? response.statusText);
    if (response.status === 401 && error.code === "not_authenticated") onSessionLost();
    throw error;
  }
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
