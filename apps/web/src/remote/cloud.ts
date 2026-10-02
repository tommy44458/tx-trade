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
