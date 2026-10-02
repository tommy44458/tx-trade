import { useCallback, useEffect, useRef, useState } from "react";
import { uiText } from "./i18n/index.ts";
import { openAuthorization } from "./desktop";
import { AnalysisSpinner } from "./AnalysisProgress";

type CloudAccountStatus = {
  configured: boolean;
  signed_in: boolean;
  pending: boolean;
  error: string | null;
  profile: { email: string | null; display_name: string | null; picture_url: string | null } | null;
  expires_at: number | null;
};

type RemoteStatus = { enabled: boolean; state: string; error: string | null; device_id: string | null };
type Tone = "ok" | "pending" | "warn" | "off";

const SIGN_IN_POLL_MS = 1000;
// Poll quickly while the link is changing, slowly once it has settled.
const REMOTE_POLL_MS = { changing: 800, settled: 5000 };
const SETTLED = new Set(["connected", "disabled", "subscription_required", "device_limit", "signed_out", "error"]);

function remoteView(remote: RemoteStatus): { tone: Tone; text: string } {
  if (!remote.enabled) return { tone: "off", text: uiText("已關閉：手機無法存取這台電腦。") };
  switch (remote.state) {
    case "connected": return { tone: "ok", text: uiText("已連線：可從手機查看這台電腦與發起分析。") };
    case "registering": return { tone: "pending", text: uiText("正在把這台電腦註冊到雲端帳戶…") };
    case "reconnecting": return { tone: "pending", text: uiText("連線中斷，正在自動重新連線…") };
    case "device_already_connected": return { tone: "pending", text: uiText("這台電腦已有另一個連線，稍後會自動重試。") };
    case "subscription_required": return { tone: "warn", text: uiText("遠端存取需要有效的訂閱方案。") };
    case "device_limit": return { tone: "warn", text: uiText("此帳戶可註冊的電腦已達上限；請先在其他電腦關閉遠端存取。") };
    case "signed_out": return { tone: "warn", text: uiText("雲端登入已失效，請重新登入。") };
    case "error": return { tone: "warn", text: errorText(remote.error ?? "") };
    default: return { tone: "pending", text: uiText("正在連線到雲端…") };
  }
}

function errorText(code: string): string {
  switch (code) {
    case "timeout": return uiText("登入逾時，請重新登入。");
    case "cancelled": return uiText("登入已取消或未完成，請重新登入。");
    case "rejected": return uiText("雲端服務未接受這次登入，請重新登入。");
    case "invalid_response": return uiText("雲端服務回應不完整，請重新登入。");
    case "storage_failed": return uiText("無法在這台電腦保存登入資訊；請確認資料目錄可寫入後重試。");
    case "misconfigured": return uiText("雲端服務位址設定不正確。");
    default: return uiText("無法連線到雲端服務，請稍後重試。");
  }
}

async function request(path: string, method = "GET"): Promise<CloudAccountStatus> {
  const response = await fetch(`/api/v1/cloud-account${path}`, { method });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(typeof body?.detail === "string" ? body.detail : "service_unavailable");
  return (body.status ?? body) as CloudAccountStatus;
}

async function readRemote(): Promise<RemoteStatus | null> {
  const response = await fetch("/api/v1/cloud-account/remote");
  return response.ok ? response.json() : null;
}

function GoogleMark() {
  return (
    <svg className="cloud-google-mark" viewBox="0 0 18 18" aria-hidden="true">
      <path fill="#4285F4" d="M17.64 9.2c0-.64-.06-1.25-.16-1.84H9v3.48h4.84a4.14 4.14 0 0 1-1.8 2.72v2.26h2.92c1.7-1.57 2.68-3.88 2.68-6.62Z" />
      <path fill="#34A853" d="M9 18c2.43 0 4.47-.8 5.96-2.18l-2.92-2.26c-.8.54-1.84.86-3.04.86-2.34 0-4.33-1.58-5.04-3.7H.96v2.33A9 9 0 0 0 9 18Z" />
      <path fill="#FBBC05" d="M3.96 10.72A5.4 5.4 0 0 1 3.68 9c0-.6.1-1.18.28-1.72V4.95H.96A9 9 0 0 0 0 9c0 1.45.35 2.83.96 4.05l3-2.33Z" />
      <path fill="#EA4335" d="M9 3.58c1.32 0 2.5.45 3.44 1.35l2.58-2.58A9 9 0 0 0 .96 4.95l3 2.33C4.67 5.16 6.66 3.58 9 3.58Z" />
    </svg>
  );
}

/** Optional Google sign-in for the txinTrade cloud; local analysis never depends on it. */
export default function CloudAccountSection({ onChanged }: { onChanged: () => void }) {
  const [status, setStatus] = useState<CloudAccountStatus | null>(null);
  const [busy, setBusy] = useState<"" | "sign-in" | "sign-out" | "cancel" | "reopen">("");
  const [error, setError] = useState("");
  const [startUrl, setStartUrl] = useState("");
  const [remote, setRemote] = useState<RemoteStatus | null>(null);
  const [toggling, setToggling] = useState(false);
  const togglingNow = useRef(false);
  const previous = useRef<CloudAccountStatus | null>(null);
  const changed = useRef(onChanged);
  useEffect(() => { changed.current = onChanged; }, [onChanged]);

  const apply = useCallback((next: CloudAccountStatus) => {
    const before = previous.current;
    previous.current = next;
    setStatus(next);
    if (before && before.signed_in !== next.signed_in) changed.current();
    // Sign-in finished in the system browser: bring the app back in front.
    if (before?.pending && next.signed_in) void window.tradeHelper?.focusWindow?.();
  }, []);

  const refresh = useCallback(() => request("").then(apply), [apply]);

  useEffect(() => {
    refresh().catch((reason: Error) => setError(errorText(reason.message)));
  }, [refresh]);

  // While the browser is open, check often, and at once when the user returns to the app.
  useEffect(() => {
    if (!status?.pending) return;
    const check = () => { refresh().catch(() => {}); };
    const timer = window.setInterval(check, SIGN_IN_POLL_MS);
    window.addEventListener("focus", check);
    document.addEventListener("visibilitychange", check);
    return () => {
      window.clearInterval(timer);
      window.removeEventListener("focus", check);
      document.removeEventListener("visibilitychange", check);
    };
  }, [status?.pending, refresh]);

  const signedIn = !!status?.signed_in;
  const remoteState = remote ? `${remote.enabled}:${remote.state}` : "";
  useEffect(() => {
    if (!signedIn) return;
    let active = true;
    let timer = 0;
    const read = () => readRemote()
      // A poll that started before a switch would undo its optimistic state.
      .then((value) => { if (active && value && !togglingNow.current) setRemote(value); })
      .catch(() => {})
      .finally(() => {
        if (!active) return;
        const settled = SETTLED.has(remoteState.split(":")[1] ?? "");
        timer = window.setTimeout(read, settled ? REMOTE_POLL_MS.settled : REMOTE_POLL_MS.changing);
      });
    const first = window.setTimeout(read, remoteState ? REMOTE_POLL_MS.changing : 0);
    return () => { active = false; window.clearTimeout(first); window.clearTimeout(timer); };
  }, [signedIn, remoteState]);

  async function run(kind: Exclude<typeof busy, "">, action: () => Promise<void>) {
    setBusy(kind);
    setError("");
    try { await action(); }
    catch (reason) { setError(errorText((reason as Error).message)); }
    finally { setBusy(""); }
  }

  const signIn = () => run("sign-in", async () => {
    const response = await fetch("/api/v1/cloud-account/sign-in", { method: "POST" });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(typeof body?.detail === "string" ? body.detail : "service_unavailable");
    setStartUrl(body.start_url);
    apply(body.status);
    await openAuthorization(body.start_url);
  });

  const toggleRemote = async (enabled: boolean) => {
    if (!remote) return;
    const before = remote;
    // Flip at once; the server confirms within a moment.
    setRemote({ ...remote, enabled, state: enabled ? "connecting" : "disabled", error: null });
    setToggling(true);
    togglingNow.current = true;
    setError("");
    try {
      const response = await fetch(`/api/v1/cloud-account/remote/${enabled ? "enable" : "disable"}`, { method: "POST" });
      if (!response.ok) throw new Error("service_unavailable");
      setRemote(await response.json());
    } catch (reason) {
      setRemote(before);
      setError(errorText((reason as Error).message));
    } finally {
      togglingNow.current = false;
      setToggling(false);
    }
  };

  const profile = status?.profile;
  const name = profile?.display_name || profile?.email || "";
  const stateLabel = status?.signed_in ? uiText("已登入") : status?.pending ? uiText("登入中") : uiText("未登入");
  const shownError = error || (status?.error && !status.pending ? errorText(status.error) : "");
  const view = remote ? remoteView(remote) : null;

  return (
    <section className="panel settings-section" aria-labelledby="settings-cloud-title">
      <div className="panel-head">
        <h2 id="settings-cloud-title">{uiText("雲端帳戶")}<small className="settings-optional">{uiText("選填")}</small></h2>
        <span className={`settings-status${status?.signed_in ? " connected" : ""}`}>{stateLabel}</span>
      </div>
      <div className="settings-account cloud-account">
        {status?.signed_in ? (
          <>
            <div className="cloud-identity">
              <span className="avatar" aria-hidden="true">{Array.from(name || "?")[0].toUpperCase()}</span>
              <span className="cloud-identity-text">
                <strong>{name}</strong>
                {profile?.email && profile.email !== name && <small>{profile.email}</small>}
              </span>
              <button type="button" className="settings-secondary cloud-sign-out" disabled={!!busy}
                onClick={() => void run("sign-out", async () => {
                  apply(await request("/sign-out", "POST"));
                  setRemote(null);
                })}>
                {busy === "sign-out" ? uiText("正在登出…") : uiText("登出")}
              </button>
            </div>
            {remote && view ? (
              <div className="cloud-remote">
                <div className="cloud-remote-row">
                  <span className="cloud-remote-copy">
                    <span className="cloud-remote-name" id="cloud-remote-name">{uiText("手機遠端存取")}</span>
                    <span className="cloud-remote-purpose" id="cloud-remote-purpose">
                      {uiText("只允許查看狀態、持倉與分析報告，以及發起市場分析；不會讀取金鑰、檔案或修改設定。App 需保持開啟，電腦主動連線到雲端，不開放任何連入的通訊埠。")}
                    </span>
                  </span>
                  <button type="button" role="switch" className="cloud-switch" aria-checked={remote.enabled}
                    aria-labelledby="cloud-remote-name" aria-describedby="cloud-remote-purpose" disabled={toggling}
                    onClick={() => void toggleRemote(!remote.enabled)}>
                    <span className="cloud-switch-thumb" aria-hidden="true" />
                  </button>
                </div>
                <p className="cloud-remote-status" data-tone={view.tone} role="status">
                  <span className="cloud-remote-dot" aria-hidden="true" />{view.text}
                </p>
              </div>
            ) : (
              <p className="cloud-remote-status" data-tone="pending" role="status">
                <span className="cloud-remote-dot" aria-hidden="true" />{uiText("正在讀取遠端存取狀態…")}
              </p>
            )}
          </>
        ) : status?.pending ? (
          <div className="cloud-pending" role="status">
            <AnalysisSpinner />
            <span className="cloud-pending-copy">
              <strong>{uiText("請在瀏覽器完成 Google 登入")}</strong>
              <small>{uiText("完成後會自動回到這裡，不需要手動切換。")}</small>
            </span>
            <span className="cloud-pending-actions">
              {startUrl && (
                <button type="button" className="cloud-link-button" disabled={!!busy}
                  onClick={() => void run("reopen", () => openAuthorization(startUrl))}>{uiText("重新開啟登入頁")}</button>
              )}
              <button type="button" className="cloud-link-button" disabled={!!busy}
                onClick={() => void run("cancel", async () => apply(await request("/sign-in/cancel", "POST")))}>{uiText("取消")}</button>
            </span>
          </div>
        ) : (
          <>
            <p className="settings-help">
              {uiText("登入後可從手機查看這台電腦的持倉與分析，並遠端發起分析。本機分析不需要登入；登入資訊只加密保存在這台電腦。")}
            </p>
            <button type="button" className="cloud-google-button" disabled={!!busy || !status?.configured}
              onClick={() => void signIn()}>
              {busy === "sign-in" ? <AnalysisSpinner /> : <GoogleMark />}
              <span>{busy === "sign-in" ? uiText("正在開啟瀏覽器…") : uiText("使用 Google 登入")}</span>
            </button>
          </>
        )}
        {shownError && <p className="settings-inline-error" role="alert">{shownError}</p>}
      </div>
    </section>
  );
}
