// Thin fetch wrapper. Attaches the session token and turns API errors into ApiError.
import { session } from "./session";

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
  options: { method?: string; body?: unknown; form?: Record<string, string> } = {},
): Promise<T> {
  const headers: Record<string, string> = {};
  const token = session.token();
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
  const payload = response.headers.get("content-type")?.includes("json")
    ? await response.json()
    : null;
  if (!response.ok) {
    const error = new ApiError(response.status, payload?.detail ?? response.statusText);
    if (response.status === 401 && token && error.code === "not_authenticated") onSessionLost();
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
