import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { uiLocale, uiText } from "../i18n/index.ts";
import { AnalysisSpinner } from "../AnalysisProgress";
import App from "../App";
import GoogleMark from "../GoogleMark";
import { setRemoteTransport } from "../transport.ts";
import {
  CloudError,
  RelaySocket,
  hasRemoteAccess,
  listDevices,
  openBillingPortal,
  readMe,
  relayTransport,
  runCommand,
  signInUrl,
  signOut,
  startCheckout,
  type Device,
  type Me,
  type Plan,
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
    case "billing_unavailable":
    case "billing_not_configured":
      return uiText("付款服務暫時無法使用，請稍後再試。");
    case "unauthenticated":
    case "session_expired":
      return uiText("雲端登入已失效，請重新登入。");
    default: return uiText("操作未完成，請稍後重試。");
  }
}

const describe = (error: unknown) => errorText(error instanceof CloudError ? error.code : "");

/** The signed-in Google account, as the account menus show it. */
function identityOf(me: Me) {
  const name = me.profile?.display_name || me.profile?.email || "";
  const email = me.profile?.email ?? null;
  return name ? { initial: Array.from(name)[0].toUpperCase(), name, detail: email && email !== name ? email : null } : null;
}

/**
 * The account at the top right of every screen outside the workspace (offline computer, no
 * subscription, a failed check), so you can always see who is signed in and sign out.
 */
function HeaderAccount({ me, device, devices, onChoose, onSignOut }: {
  me: Me;
  device: Device | null;
  devices: Device[] | null;
  onChoose: (id: string) => void;
  onSignOut: () => void;
}) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const close = (event: Event) => {
      if (event instanceof KeyboardEvent ? event.key === "Escape" : !root.current?.contains(event.target as Node))
        setOpen(false);
    };
    document.addEventListener("pointerdown", close);
    document.addEventListener("keydown", close);
    return () => { document.removeEventListener("pointerdown", close); document.removeEventListener("keydown", close); };
  }, [open]);
  const identity = identityOf(me);
  return (
    <div className="remote-account" ref={root} onClick={(event) => {
      if ((event.target as Element).closest("[data-menu-close]")) setOpen(false);
    }}>
      <button type="button" className="remote-account-trigger" aria-expanded={open} aria-controls="remote-account-panel"
        aria-label={uiText("帳戶與設定")} onClick={() => setOpen((value) => !value)}>
        <span className="avatar" aria-hidden="true">{identity?.initial ?? "?"}</span>
        {identity && <span className="remote-account-name">{identity.name}</span>}
      </button>
      {open && (
        <div className="account-menu-panel remote-account-panel" id="remote-account-panel">
          {identity && (
            <div className="account-menu-identity">
              <span className="avatar" aria-hidden="true">{identity.initial}</span>
              <span>
                <strong>{identity.name}</strong>
                {identity.detail && <small>{identity.detail}</small>}
              </span>
            </div>
          )}
          {device && devices?.length ? (
            <RemoteMenu device={device} devices={devices} billing={Boolean(me.billing)} onChoose={onChoose} onSignOut={onSignOut} />
          ) : (
            <>
              <hr className="account-menu-separator" />
              {me.billing && <ManageBilling />}
              <button type="button" className="account-menu-item destructive" data-menu-close onClick={onSignOut}>{uiText("登出")}</button>
            </>
          )}
        </div>
      )}
    </div>
  );
}

function Screen({ children, account }: { children: ReactNode; account?: ReactNode }) {
  return (
    <div className="remote-shell">
      <header className="remote-top"><span className="brand-wordmark">txin<strong>Trade</strong></span>{account}</header>
      <main className="remote-main">{children}</main>
    </div>
  );
}

function Loading({ label }: { label: string }) {
  return <p className="remote-loading" role="status"><AnalysisSpinner />{label}</p>;
}

/** Opens Creem's customer portal: cancel, change the card, invoices. */
function ManageBilling() {
  return (
    <button type="button" className="account-menu-item" data-menu-close
      onClick={() => void openBillingPortal().catch((reason) => window.alert(describe(reason)))}>{uiText("管理訂閱")}</button>
  );
}

/** Account menu items on the remote page: the computers to reach, the subscription, and sign-out. */
function RemoteMenu({ device, devices, billing, onChoose, onSignOut }: {
  device: Device;
  devices: Device[];
  billing: boolean;
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
      {billing && <ManageBilling />}
      <button type="button" className="account-menu-item destructive" data-menu-close onClick={onSignOut}>{uiText("登出")}</button>
    </>
  );
}

const PLAN_COPY = [
  { plan: "monthly", name: "遠端存取", price: "US$3.99／月", note: "以美元按月計費，隨時可取消。" },
] as const;

/** The remote access plan; it goes to Creem's checkout for the signed-in account. */
function Plans({ subscribedBefore }: { subscribedBefore: boolean }) {
  const [busy, setBusy] = useState<Plan | null>(null);
  const [error, setError] = useState("");
  const choose = (plan: Plan) => {
    setBusy(plan);
    setError("");
    startCheckout(plan).catch((reason) => {
      setBusy(null);
      // A subscription that still exists (e.g. a failed renewal) is fixed in the portal.
      if (reason instanceof CloudError && reason.code === "already_subscribed")
        return openBillingPortal().catch((portal) => setError(describe(portal)));
      setError(describe(reason));
    });
  };
  return (
    <section className="remote-welcome">
      <h2>{uiText("訂閱遠端存取")}</h2>
      <p>{uiText("從任何瀏覽器使用電腦上的 txinTrade：查看分析與持倉、遠端發起分析、即時追問。分析仍在你的電腦上執行。")}</p>
      {error && <p className="remote-notice warn" role="alert">{error}</p>}
      <div className="remote-plans">
        {PLAN_COPY.map((item) => (
          <button type="button" key={item.plan} className="remote-plan" disabled={busy !== null}
            aria-busy={busy === item.plan} onClick={() => choose(item.plan)}>
            <strong>{uiText(item.name)}</strong>
            <span className="remote-plan-price">{uiText(item.price)}</span>
            <small>{busy === item.plan ? uiText("正在前往付款頁…") : uiText(item.note)}</small>
          </button>
        ))}
      </div>
      <p className="remote-plan-note">{uiText("付款由 Creem 處理，可隨時取消；首次付款 7 天內可全額退款。")}</p>
      {subscribedBefore && (
        <button type="button" className="remote-secondary"
          onClick={() => void openBillingPortal().catch((reason) => setError(describe(reason)))}>{uiText("管理訂閱")}</button>
      )}
    </section>
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
  const sockets = useRef<RelaySocket | null>(null);
  useEffect(() => () => sockets.current?.close(), []);
  useEffect(() => {
    let active = true;
    // An app from before full remote screens only knows the five original commands.
    runCommand<{ capabilities?: string[] }>(device.id, { operation: "status.read" })
      .then((status) => {
        if (!active) return;
        if (status.capabilities?.includes("api.request")) {
          const socket = new RelaySocket(device.id);
          // An app without live replies reads saved ones instead (the discussion polls).
          const streams = status.capabilities.includes("stream");
          setRemoteTransport(relayTransport(device.id, { describe: errorText, onSignedOut }),
            (path, handlers) => {
              if (streams) return socket.open(path, handlers);
              window.setTimeout(handlers.onError, 0);
              return () => {};
            });
          sockets.current?.close();
          sockets.current = socket;
          setCheck({ device: device.id, state: "ready", attempt });
        } else setCheck({ device: device.id, state: "outdated", attempt });
      })
      .catch((reason) => {
        if (active) setCheck({ device: device.id, state: "failed", error: describe(reason), attempt });
      });
    return () => { active = false; };
  }, [device.id, onSignedOut, attempt]);

  const identity = identityOf(me);
  const name = identity?.name ?? "";
  const email = me.profile?.email ?? null;
  const account = <HeaderAccount me={me} device={device} devices={devices} onChoose={onChoose} onSignOut={onSignOut} />;
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
    return <Screen account={account}><Loading label={uiText("正在連線到你的電腦…")} /></Screen>;
  if (check.state !== "ready")
    return (
      <Screen account={account}>
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
        remoteMenu={<RemoteMenu device={device} devices={devices} billing={Boolean(me.billing)} onChoose={onChoose} onSignOut={onSignOut} />}
        remoteStatus={<span className="remote-status" title={device.label}>
          <i className={device.online ? undefined : "offline"} />{" "}{uiText("遠端模式")}<b>{device.label}</b>
        </span>} />
    </>
  );
}

/**
 * An account that has never connected a computer: remote screens run on the desktop app, so
 * say exactly where to sign in with this same account and turn remote access on.
 */
function NoComputer({ email, onRefresh }: { email: string | null; onRefresh: () => void }) {
  const download = uiLocale() === "en-US" ? "https://txintrade.com/download" : "https://txintrade.com/zh-TW/download";
  return (
    <section className="remote-welcome remote-setup">
      <h2>{uiText("先在電腦上打開遠端存取")}</h2>
      <p>{email
        ? uiText("你登入的是 {{p0}}，這個帳號還沒有連線過任何電腦。遠端網頁使用的是你電腦上的 txinTrade，請先在電腦上完成以下設定：", { p0: email })
        : uiText("這個帳號還沒有連線過任何電腦。遠端網頁使用的是你電腦上的 txinTrade，請先在電腦上完成以下設定：")}</p>
      <ol className="remote-steps">
        <li>
          <strong>{uiText("在電腦上開啟 txinTrade")}</strong>
          <span>{uiText("還沒安裝？")}{" "}<a href={download}>{uiText("下載 txinTrade")}</a></span>
        </li>
        <li>
          <strong>{uiText("用同一個 Google 帳號登入")}</strong>
          <span>{email
            ? uiText("點左下角的帳戶，選「登入雲端帳戶以遠端使用」，用 {{p0}} 登入。", { p0: email })
            : uiText("點左下角的帳戶，選「登入雲端帳戶以遠端使用」，用同一個帳號登入。")}</span>
        </li>
        <li>
          <strong>{uiText("打開「遠端存取」")}</strong>
          <span>{uiText("在同一個帳戶選單打開「遠端存取」，並讓 txinTrade 保持開啟。這個頁面會自動連上你的電腦。")}</span>
        </li>
      </ol>
      <button type="button" className="remote-secondary" onClick={onRefresh}>{uiText("重新整理")}</button>
    </section>
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
  // Back from Creem's checkout: access opens when the webhook lands, usually within seconds.
  const [confirming, setConfirming] = useState(() => new URLSearchParams(window.location.search).get("billing") === "success");

  const refreshDevices = useCallback(() => listDevices().then(setDevices).catch((reason) => setError(describe(reason))), []);
  const signedOut = useCallback(() => { setMe(null); setDevices(null); }, []);

  useEffect(() => {
    if (window.location.search) window.history.replaceState(null, "", window.location.pathname);
    readMe().then((value) => {
      setSubscribed(hasRemoteAccess(value));
      setMe(value);
      void refreshDevices();
    }).catch((reason) => {
      setMe(null);
      if (!(reason instanceof CloudError && reason.status === 401)) setError(describe(reason));
    });
  }, [refreshDevices]);

  useEffect(() => {
    if (!confirming || !me || subscribed) return;
    let tries = 0;
    const timer = window.setInterval(() => {
      tries += 1;
      readMe().then((value) => {
        if (!hasRemoteAccess(value)) return;
        setMe(value);
        setSubscribed(true);
      }).catch(() => {});
      if (tries >= 15) {
        window.clearInterval(timer);
        setConfirming(false);
        setError(uiText("付款已完成，開通仍在處理中。請稍後重新整理此頁。"));
      }
    }, 2000);
    return () => window.clearInterval(timer);
  }, [confirming, me, subscribed]);

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
    <Screen account={me ? <HeaderAccount me={me} device={device} devices={devices} onChoose={choose}
      onSignOut={() => void signOut().finally(signedOut)} /> : undefined}>
      {error && <p className="remote-notice warn" role="alert">{error}</p>}
      {me === undefined ? <Loading label={uiText("正在連線到 txinTrade 雲端…")} /> : me === null ? (
        <section className="remote-welcome">
          <h1>{uiText("從任何裝置使用你的 txinTrade")}</h1>
          <p>{uiText("查看電腦上的分析與持倉，或遠端發起新的分析。分析仍在你的電腦上執行，金鑰與完整資料不會離開電腦。")}</p>
          <a className="cloud-google-button" href={signInUrl()}><GoogleMark /><span>{uiText("使用 Google 登入")}</span></a>
        </section>
      ) : !subscribed ? (
        confirming ? <Loading label={uiText("正在確認付款…")} /> : <Plans subscribedBefore={Boolean(me.billing)} />
      ) : !devices ? <Loading label={uiText("正在讀取你的電腦…")} /> : !device ? (
        <NoComputer email={me.profile?.email ?? null} onRefresh={() => void refreshDevices()} />
      ) : (
        <section className="remote-welcome">
          <h2>{uiText("電腦目前離線")}</h2>
          <p>{uiText("電腦需要開著 txinTrade 並打開「遠端存取」；電腦睡眠時也無法連線。")}</p>
          {devices.length > 1 && (
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
