import { useCallback, useEffect, useState, type ReactNode } from "react";
import { uiText } from "../i18n/index.ts";
import { AnalysisSpinner } from "../AnalysisProgress";
import App from "../App";
import GoogleMark from "../GoogleMark";
import { setRemoteTransport } from "../transport.ts";
import {
  CloudError,
  listDevices,
  readMe,
  relayTransport,
  runCommand,
  signInUrl,
  signOut,
  type Device,
  type Me,
} from "./cloud";
import "./RemoteApp.css";

const DEVICE_KEY = "txintrade.remote.device";

function remember(key: string, value?: string): string | null {
  try {
    if (value !== undefined) window.localStorage.setItem(key, value);
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

function errorText(code: string): string {
  switch (code) {
    case "device_offline":
    case "device_disconnected":
      return uiText("電腦目前離線。請確認電腦開著 txinTrade，且已打開遠端存取。");
    case "device_timeout":
    case "expired":
      return uiText("電腦沒有及時回應，請重試。");
    case "subscription_required": return uiText("遠端存取需要有效的訂閱方案。");
    case "local_unavailable": return uiText("電腦上找不到這筆資料，或 App 正在更新。");
    case "unsupported_operation":
    case "invalid_request":
      return uiText("電腦上的 txinTrade 版本不支援這個操作，請更新 App。");
    case "request_limit": return uiText("短時間內的請求太多，請稍候再試。");
    case "response_too_large": return uiText("這筆資料太大，請在電腦上查看。");
    case "network": return uiText("無法連線到 txinTrade 雲端，請檢查網路後重試。");
    case "unauthenticated":
    case "session_expired":
      return uiText("雲端登入已失效，請重新登入。");
    default: return uiText("操作未完成，請稍後重試。");
  }
}

const describe = (error: unknown) => errorText(error instanceof CloudError ? error.code : "");

function Screen({ children }: { children: ReactNode }) {
  return (
    <div className="remote-shell">
      <header className="remote-top"><span className="brand-wordmark">txin<strong>Trade</strong></span></header>
      <main className="remote-main">{children}</main>
    </div>
  );
}

function Loading({ label }: { label: string }) {
  return <p className="remote-loading" role="status"><AnalysisSpinner />{label}</p>;
}

/** Account menu items on the remote page: the computers to reach, and sign-out. */
function RemoteMenu({ device, devices, onChoose, onSignOut }: {
  device: Device;
  devices: Device[];
  onChoose: (id: string) => void;
  onSignOut: () => void;
}) {
  return (
    <>
      <p className="account-menu-heading">{uiText("電腦")}</p>
      {devices.map((item) => (
        <button type="button" key={item.id} className="account-menu-item" data-menu-close
          aria-current={item.id === device.id ? "true" : undefined}
          onClick={() => { if (item.id !== device.id) onChoose(item.id); }}>
          <span className="account-menu-check" aria-hidden="true">{item.id === device.id ? "✓" : ""}</span>
          <span className="account-menu-item-copy">{item.label}
            <small><span className={`remote-device-dot${item.online ? " online" : ""}`} aria-hidden="true" />
              {item.online ? uiText("在線") : uiText("離線")}</small></span>
        </button>
      ))}
      <hr className="account-menu-separator" />
      <button type="button" className="account-menu-item destructive" data-menu-close onClick={onSignOut}>{uiText("登出")}</button>
    </>
  );
}

type Check = "checking" | "ready" | "outdated" | "failed";

/** The desktop app's own screens, reaching the chosen computer through the relay. */
function RemoteWorkspace({ device, devices, me, onChoose, onSignOut, onSignedOut }: {
  device: Device;
  devices: Device[];
  me: Me;
  onChoose: (id: string) => void;
  onSignOut: () => void;
  onSignedOut: () => void;
}) {
  const [check, setCheck] = useState<{ device: string; state: Check; error?: string; attempt: number }>(
    { device: "", state: "checking", attempt: 0 });
  const attempt = check.attempt;
  useEffect(() => {
    let active = true;
    // An app from before full remote screens only knows the five original commands.
    runCommand<{ capabilities?: string[] }>(device.id, { operation: "status.read" })
      .then((status) => {
        if (!active) return;
        if (status.capabilities?.includes("api.request")) {
          setRemoteTransport(relayTransport(device.id, { describe: errorText, onSignedOut }));
          setCheck({ device: device.id, state: "ready", attempt });
        } else setCheck({ device: device.id, state: "outdated", attempt });
      })
      .catch((reason) => {
        if (active) setCheck({ device: device.id, state: "failed", error: describe(reason), attempt });
      });
    return () => { active = false; };
  }, [device.id, onSignedOut, attempt]);

  const name = me.profile?.display_name || me.profile?.email || "";
  const email = me.profile?.email ?? null;
  const identity = name ? { initial: Array.from(name)[0].toUpperCase(), name, detail: email && email !== name ? email : null } : null;
  const section = (
    <section className="panel settings-section" aria-labelledby="settings-remote-title">
      <div className="panel-head">
        <h2 id="settings-remote-title">{uiText("遠端連線")}</h2>
        <span className={`settings-status${device.online ? " connected" : ""}`}>
          {device.online ? uiText("在線") : uiText("離線")}
        </span>
      </div>
      <div className="settings-account cloud-account">
        <div className="cloud-group">
          <div className="cloud-row">
            <span className="avatar" aria-hidden="true">{Array.from(name || "?")[0].toUpperCase()}</span>
            <span className="cloud-row-copy">
              <strong>{name}</strong>
              {email && email !== name && <small>{email}</small>}
            </span>
          </div>
          <label className="cloud-row">
            <span className="cloud-row-copy">
              <span>{uiText("電腦")}</span>
              <small className="cloud-remote-status" data-tone={device.online ? "ok" : "off"}>
                <span className="cloud-remote-dot" aria-hidden="true" />{device.online ? uiText("在線") : uiText("離線")}
              </small>
            </span>
            <select className="cloud-row-select" value={device.id} onChange={(event) => onChoose(event.target.value)}
              disabled={devices.length < 2}>
              {devices.map((item) => <option key={item.id} value={item.id}>{item.label}</option>)}
            </select>
          </label>
        </div>
        <p className="cloud-footnote">
          {uiText("這些畫面直接讀取你電腦上的 txinTrade；分析在電腦上執行。金鑰、AI 帳號與雲端帳戶只能在電腦上設定。外觀與語言只套用在這個瀏覽器。")}
        </p>
        <div className="cloud-group">
          <button type="button" className="cloud-row cloud-row-button destructive" onClick={onSignOut}>{uiText("登出")}</button>
        </div>
      </div>
    </section>
  );

  if (check.device !== device.id || check.state === "checking")
    return <Screen><Loading label={uiText("正在連線到你的電腦…")} /></Screen>;
  if (check.state !== "ready")
    return (
      <Screen>
        <p className="remote-notice warn" role="alert">
          {check.state === "outdated" ? uiText("電腦上的 txinTrade 版本不支援這個操作，請更新 App。") : check.error}
        </p>
        <button type="button" className="remote-secondary"
          onClick={() => setCheck({ device: "", state: "checking", attempt: attempt + 1 })}>{uiText("重試")}</button>
      </Screen>
    );
  return (
    <>
      {!device.online && (
        <p className="remote-offline-banner" role="alert">{uiText("電腦目前離線。請確認電腦開著 txinTrade，且已打開遠端存取。")}</p>
      )}
      <App key={device.id} remoteSection={section}
        remoteIdentity={identity}
        remoteMenu={<RemoteMenu device={device} devices={devices} onChoose={onChoose} onSignOut={onSignOut} />}
        remoteStatus={<span className="remote-status" title={device.label}>
          <i className={device.online ? undefined : "offline"} />{" "}{uiText("遠端模式")}<b>{device.label}</b>
        </span>} />
    </>
  );
}

/** Remote page for the txinTrade cloud: the desktop app's screens, run on your own computer. */
export default function RemoteApp() {
  const [me, setMe] = useState<Me | null | undefined>(undefined);
  const [subscribed, setSubscribed] = useState(false);
  const [devices, setDevices] = useState<Device[] | null>(null);
  const [deviceId, setDeviceId] = useState<string | null>(() => remember(DEVICE_KEY));
  const [error, setError] = useState(() => new URLSearchParams(window.location.search).get("sign_in_error")
    ? uiText("登入未完成，請重試。") : "");

  const refreshDevices = useCallback(() => listDevices().then(setDevices).catch((reason) => setError(describe(reason))), []);
  const signedOut = useCallback(() => { setMe(null); setDevices(null); }, []);

  useEffect(() => {
    if (window.location.search) window.history.replaceState(null, "", window.location.pathname);
    readMe().then((value) => {
      setSubscribed(value.entitlements.some((item) => item.feature === "remote_access" && item.status === "active"
        && (item.expires_at === null || item.expires_at > Date.now())));
      setMe(value);
      void refreshDevices();
    }).catch((reason) => {
      setMe(null);
      if (!(reason instanceof CloudError && reason.status === 401)) setError(describe(reason));
    });
  }, [refreshDevices]);

  // Keep the online state current, and re-check at once when the page comes back.
  useEffect(() => {
    if (!me) return;
    const check = () => { if (document.visibilityState === "visible") void refreshDevices(); };
    document.addEventListener("visibilitychange", check);
    const timer = window.setInterval(check, 15000);
    return () => { document.removeEventListener("visibilitychange", check); window.clearInterval(timer); };
  }, [me, refreshDevices]);

  const device = devices?.find((item) => item.id === deviceId) ?? devices?.find((item) => item.online) ?? devices?.[0] ?? null;
  const choose = (id: string) => { setDeviceId(id); remember(DEVICE_KEY, id); };
  const [opened, setOpened] = useState<string | null>(null);
  // Once open, a computer that drops offline keeps its screens with a banner instead of vanishing.
  if (device?.online && opened !== device.id) setOpened(device.id);

  if (me && subscribed && devices && device && (device.online || opened === device.id))
    return (
      <RemoteWorkspace device={device} devices={devices} me={me} onChoose={choose} onSignedOut={signedOut}
        onSignOut={() => void signOut().finally(signedOut)} />
    );
  return (
    <Screen>
      {error && <p className="remote-notice warn" role="alert">{error}</p>}
      {me === undefined ? <Loading label={uiText("正在連線到 txinTrade 雲端…")} /> : me === null ? (
        <section className="remote-welcome">
          <h1>{uiText("從任何裝置使用你的 txinTrade")}</h1>
          <p>{uiText("查看電腦上的分析與持倉，或遠端發起新的分析。分析仍在你的電腦上執行，金鑰與完整資料不會離開電腦。")}</p>
          <a className="cloud-google-button" href={signInUrl()}><GoogleMark /><span>{uiText("使用 Google 登入")}</span></a>
        </section>
      ) : !subscribed ? (
        <p className="remote-notice warn" role="alert">{uiText("遠端存取需要有效的訂閱方案。")}</p>
      ) : !devices ? <Loading label={uiText("正在讀取你的電腦…")} /> : (
        <section className="remote-welcome">
          <h2>{device ? uiText("電腦目前離線") : uiText("還沒有可連線的電腦")}</h2>
          <p>{device
            ? uiText("電腦需要開著 txinTrade 並打開「遠端存取」；電腦睡眠時也無法連線。")
            : uiText("在電腦版 txinTrade 的「設定 → 雲端帳戶」登入同一個 Google 帳號，並打開「遠端存取」。")}</p>
          {device && devices.length > 1 && (
            <select value={device.id} onChange={(event) => choose(event.target.value)} aria-label={uiText("選擇電腦")}>
              {devices.map((item) => <option key={item.id} value={item.id}>{item.label}</option>)}
            </select>
          )}
          <button type="button" className="remote-secondary" onClick={() => void refreshDevices()}>{uiText("重新整理")}</button>
        </section>
      )}
    </Screen>
  );
}
