import { useCallback, useEffect, useRef, useState } from "react";
import { uiText } from "./i18n/index.ts";
import { copyText } from "./copyText";
import { openAuthorization } from "./desktop";
import { AnalysisSpinner } from "./AnalysisProgress";
import GoogleMark from "./GoogleMark";
import { apiFetch } from "./transport.ts";
import { cloudErrorText, remoteView, type RemoteStatus } from "./remoteAccess.ts";

type CloudAccountStatus = {
  configured: boolean;
  signed_in: boolean;
  pending: boolean;
  error: string | null;
  profile: { email: string | null; display_name: string | null; picture_url: string | null } | null;
  expires_at: number | null;
  /** The remote web page for this cloud, e.g. https://app.txintrade.com. */
  web_url?: string | null;
};

const SIGN_IN_POLL_MS = 1000;
// Poll quickly while the link is changing, slowly once it has settled.
const REMOTE_POLL_MS = { changing: 800, settled: 5000 };
const SETTLED = new Set(["connected", "disabled", "subscription_required", "device_limit", "signed_out", "error"]);

async function request(path: string, method = "GET"): Promise<CloudAccountStatus> {
  const response = await apiFetch(`/api/v1/cloud-account${path}`, { method });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(typeof body?.detail === "string" ? body.detail : "service_unavailable");
  return (body.status ?? body) as CloudAccountStatus;
}

async function readRemote(): Promise<RemoteStatus | null> {
  const response = await apiFetch("/api/v1/cloud-account/remote");
  return response.ok ? response.json() : null;
}

/** Optional Google sign-in for the txinTrade cloud; local analysis never depends on it. */
/** Where to use txinTrade remotely: the address to open on any device, with Open and Copy. */
function RemoteWebRow({ url }: { url: string }) {
  const [copied, setCopied] = useState(false);
  const host = new URL(url).host;
  return (
    <div className="cloud-group">
      <div className="cloud-row">
        <span className="cloud-row-copy">
          <span>{uiText("遠端網頁")}</span>
          <small className="cloud-web-url">{host}</small>
        </span>
        <span className="cloud-row-actions">
          <button type="button" className="cloud-link-button" onClick={() => void copyText(url).then((done) => {
            setCopied(done);
            if (done) window.setTimeout(() => setCopied(false), 2000);
          })}>{copied ? uiText("已複製") : uiText("複製網址")}</button>
          <button type="button" className="cloud-link-button" onClick={() => void openAuthorization(url)}>{uiText("開啟")}</button>
        </span>
      </div>
    </div>
  );
}

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
    refresh().catch((reason: Error) => setError(cloudErrorText(reason.message)));
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
    catch (reason) { setError(cloudErrorText((reason as Error).message)); }
    finally { setBusy(""); }
  }

  const signIn = () => run("sign-in", async () => {
    const response = await apiFetch("/api/v1/cloud-account/sign-in", { method: "POST" });
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
      const response = await apiFetch(`/api/v1/cloud-account/remote/${enabled ? "enable" : "disable"}`, { method: "POST" });
      if (!response.ok) throw new Error("service_unavailable");
      setRemote(await response.json());
    } catch (reason) {
      setRemote(before);
      setError(cloudErrorText((reason as Error).message));
    } finally {
      togglingNow.current = false;
      setToggling(false);
    }
  };

  const profile = status?.profile;
  const name = profile?.display_name || profile?.email || "";
  const stateLabel = status?.signed_in ? uiText("已登入") : status?.pending ? uiText("登入中") : uiText("未登入");
  const shownError = error || (status?.error && !status.pending ? cloudErrorText(status.error) : "");
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
            <div className="cloud-group">
              <div className="cloud-row">
                <span className="avatar" aria-hidden="true">{Array.from(name || "?")[0].toUpperCase()}</span>
                <span className="cloud-row-copy">
                  <strong>{name}</strong>
                  {profile?.email && profile.email !== name && <small>{profile.email}</small>}
                </span>
              </div>
              <div className="cloud-row">
                <span className="cloud-row-copy">
                  <span id="cloud-remote-name">{uiText("遠端存取")}</span>
                  <small className="cloud-remote-status" data-tone={view?.tone ?? "pending"} role="status">
                    <span className="cloud-remote-dot" aria-hidden="true" />
                    {view?.text ?? uiText("正在讀取遠端存取狀態…")}
                  </small>
                </span>
                <button type="button" role="switch" className="cloud-switch" aria-checked={!!remote?.enabled}
                  aria-labelledby="cloud-remote-name" aria-describedby="cloud-remote-purpose" disabled={!remote || toggling}
                  onClick={() => remote && void toggleRemote(!remote.enabled)}>
                  <span className="cloud-switch-thumb" aria-hidden="true" />
                </button>
              </div>
            </div>
            {status.web_url && <RemoteWebRow url={status.web_url} />}
            <p className="cloud-footnote" id="cloud-remote-purpose">
              {uiText("只允許查看狀態、持倉與分析報告，以及發起市場分析；不會讀取金鑰、檔案或修改設定。App 需保持開啟，電腦主動連線到雲端，不開放任何連入的通訊埠。")}
            </p>
            <div className="cloud-group">
              <button type="button" className="cloud-row cloud-row-button destructive" disabled={!!busy}
                onClick={() => void run("sign-out", async () => {
                  apply(await request("/sign-out", "POST"));
                  setRemote(null);
                })}>
                {busy === "sign-out" ? uiText("正在登出…") : uiText("登出")}
              </button>
            </div>
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
              {uiText("登入後可從其他裝置的瀏覽器查看這台電腦的持倉與分析，並遠端發起分析。本機分析不需要登入；登入資訊只加密保存在這台電腦。")}
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
