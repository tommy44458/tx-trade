// Client for the txinTrade cloud, used by the remote web page only. The browser
// authenticates with the Worker's HttpOnly session cookie; no token is readable here.

// The dev server talks to `wrangler dev`; a build talks to production unless overridden.
export const CLOUD_ORIGIN = (import.meta.env.VITE_TXINTRADE_CLOUD_ORIGIN
  ?? (import.meta.env.DEV ? "http://localhost:8787" : "https://api.txintrade.com")).replace(/\/$/, "");

export class CloudError extends Error {
  code: string;
  status: number;

  constructor(code: string, status = 0) {
    super(code);
    this.code = code;
    this.status = status;
  }
}

export type Profile = { email: string | null; display_name: string | null; picture_url: string | null };
export type Me = {
  account_id: string;
  profile: Profile | null;
  entitlements: Array<{ feature: string; status: string; expires_at: number | null }>;
};
export type Device = { id: string; label: string; created_at: number; revoked_at: number | null; online: boolean };

export type Command =
  | { operation: "status.read" }
  | { operation: "positions.list" }
  | { operation: "analyses.list"; limit: number }
  | { operation: "analyses.get"; analysis_id: string }
  | { operation: "analyses.start"; market_id: string; timeframe: string; output_locale: "zh-TW" | "en-US" };

type Job = { id: string; status: "dispatched" | "accepted" | "completed" | "failed" | "expired"; result?: unknown; error?: string };

async function cloud<T>(path: string, init: RequestInit = {}): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${CLOUD_ORIGIN}${path}`, { credentials: "include", ...init });
  } catch {
    throw new CloudError("network");
  }
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new CloudError(body?.error?.code ?? "service_unavailable", response.status);
  return body as T;
}

export const signInUrl = () =>
  `${CLOUD_ORIGIN}/auth/google/start?client=web&return_to=${encodeURIComponent(window.location.origin)}`;

export const readMe = () => cloud<Me>("/api/v1/me");

export const signOut = () => cloud<unknown>("/auth/logout", { method: "POST" });

export async function listDevices(): Promise<Device[]> {
  const { devices } = await cloud<{ devices: Device[] }>("/api/v1/devices");
  return devices.filter((device) => device.revoked_at === null);
}

const wait = (ms: number) => new Promise((resolve) => window.setTimeout(resolve, ms));

/** Send one allowlisted command to a desktop and wait for its answer (the relay gives it 30 s). */
export async function runCommand<T>(deviceId: string, command: Command): Promise<T> {
  const created = await cloud<Job>(`/api/v1/devices/${deviceId}/jobs`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "Idempotency-Key": crypto.randomUUID() },
    body: JSON.stringify(command),
  });
  let job = created;
  for (let attempt = 0; attempt < 80; attempt += 1) {
    if (job.status === "completed") return job.result as T;
    if (job.status === "failed") throw new CloudError(job.error ?? "local_error");
    if (job.status === "expired") throw new CloudError("expired");
    await wait(Math.min(250 + attempt * 100, 1000));
    job = await cloud<Job>(`/api/v1/devices/${deviceId}/jobs/${created.id}`);
  }
  throw new CloudError("expired");
}

const RELAY_ERRORS = new Set([
  "device_offline", "device_timeout", "device_disconnected", "local_unavailable", "local_error",
  "unsupported_operation", "request_limit", "invalid_request", "subscription_required", "response_too_large",
]);

/**
 * Reach the computer's local API through the relay: same paths, methods and bodies
 * as the desktop app. Relay problems become `{detail}` errors the screens already show.
 */
export function relayTransport(
  deviceId: string,
  options: { describe: (code: string) => string; onSignedOut: () => void },
): (path: string, init?: RequestInit) => Promise<Response> {
  return async (path, init = {}) => {
    const url = new URL(path, "http://local");
    const headers = new Headers(init.headers);
    const key = headers.get("Idempotency-Key");
    const request = {
      method: (init.method ?? "GET").toUpperCase(),
      path: url.pathname,
      query: url.search.slice(1),
      ...(typeof init.body === "string" ? { body: JSON.parse(init.body) } : {}),
      ...(key ? { idempotency_key: key } : {}),
    };
    let response: Response;
    try {
      response = await fetch(`${CLOUD_ORIGIN}/api/v1/devices/${deviceId}/requests`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(request),
        signal: init.signal,
      });
    } catch (reason) {
      if ((reason as Error).name === "AbortError") throw reason;
      return Response.json({ detail: options.describe("network") }, { status: 503 });
    }
    if (response.ok) return response;
    const body = await response.clone().json().catch(() => null);
    const code = body?.error?.code;
    if (response.status === 401) options.onSignedOut();
    // Errors from the computer's own API pass through unchanged.
    if (typeof code !== "string") return response;
    return Response.json(
      { detail: options.describe(RELAY_ERRORS.has(code) || response.status === 401 ? code : "relay_error") },
      { status: response.status },
    );
  };
}
