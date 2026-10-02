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

const POLL_MS = 1500;

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

/** Optional Google sign-in for the txinTrade cloud; local analysis never depends on it. */
export default function CloudAccountSection({ onChanged }: { onChanged: () => void }) {
  const [status, setStatus] = useState<CloudAccountStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [startUrl, setStartUrl] = useState("");
  const signedIn = useRef<boolean | null>(null);
  const changed = useRef(onChanged);
  useEffect(() => { changed.current = onChanged; }, [onChanged]);

  const apply = useCallback((next: CloudAccountStatus) => {
    setStatus(next);
    if (signedIn.current !== null && signedIn.current !== next.signed_in) changed.current();
    signedIn.current = next.signed_in;
  }, []);

  useEffect(() => {
    request("").then(apply).catch((reason: Error) => setError(errorText(reason.message)));
  }, [apply]);

  useEffect(() => {
    if (!status?.pending) return;
    const timer = window.setInterval(() => {
      request("").then(apply).catch(() => {});
    }, POLL_MS);
    return () => window.clearInterval(timer);
  }, [status?.pending, apply]);

  async function run(action: () => Promise<void>) {
    setBusy(true);
    setError("");
    try { await action(); }
    catch (reason) { setError(errorText((reason as Error).message)); }
    finally { setBusy(false); }
  }

  const signIn = () => run(async () => {
    const response = await fetch("/api/v1/cloud-account/sign-in", { method: "POST" });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(typeof body?.detail === "string" ? body.detail : "service_unavailable");
    setStartUrl(body.start_url);
    apply(body.status);
    await openAuthorization(body.start_url);
  });

  const profile = status?.profile;
  const name = profile?.display_name || profile?.email || "";
  const stateLabel = status?.signed_in ? uiText("已登入") : status?.pending ? uiText("登入中") : uiText("未登入");
  const shownError = error || (status?.error && !status.pending ? errorText(status.error) : "");

  return (
    <section className="panel settings-section" aria-labelledby="settings-cloud-title">
      <div className="panel-head">
        <h2 id="settings-cloud-title">{uiText("雲端帳戶")}<small className="settings-optional">{uiText("選填")}</small></h2>
        <span className={`settings-status${status?.signed_in ? " connected" : ""}`}>{stateLabel}</span>
      </div>
      <div className="settings-account">
        {status?.signed_in && name && (
          <p className="settings-cloud-identity">
            <span className="avatar" aria-hidden="true">{Array.from(name)[0].toUpperCase()}</span>
            <span>
              <strong>{name}</strong>
              {profile?.email && profile.email !== name && <small>{profile.email}</small>}
            </span>
          </p>
        )}
        <p className="settings-help">
          {uiText("以 Google 帳號登入 txinTrade 雲端帳戶，為之後在手機遠端查看與發起分析做準備。本機分析不需要登入；登入資訊只加密保存在這台電腦。")}
        </p>
        {shownError && <p className="settings-inline-error" role="alert">{shownError}</p>}
        {status?.pending ? (
          <>
            <p className="settings-login-wait" role="status"><AnalysisSpinner />{uiText("等待瀏覽器完成登入…")}</p>
            <div className="settings-actions">
              {startUrl && (
                <button type="button" className="settings-secondary" disabled={busy}
                  onClick={() => void run(() => openAuthorization(startUrl))}>{uiText("重新開啟登入頁")}</button>
              )}
              <button type="button" className="settings-secondary" disabled={busy}
                onClick={() => void run(async () => apply(await request("/sign-in/cancel", "POST")))}>{uiText("取消")}</button>
            </div>
          </>
        ) : (
          <div className="settings-actions">
            {status?.signed_in ? (
              <button type="button" className="settings-secondary" disabled={busy}
                onClick={() => void run(async () => apply(await request("/sign-out", "POST")))}>{uiText("登出")}</button>
            ) : (
              <button type="button" className="action" disabled={busy || !status?.configured} onClick={() => void signIn()}>
                {uiText("使用 Google 登入")}
              </button>
            )}
          </div>
        )}
      </div>
    </section>
  );
}
