// The remote page's screens outside the workspace: welcome, setup, offline,
// subscription and payment confirmation. Presentation only; RemoteApp decides
// which one shows.
import { useEffect, type CSSProperties, type ReactNode } from "react";
import { setThemeSurface } from "./themeColor.ts";
import { languageName, setUiLocale, uiLocale, uiText } from "../i18n/index.ts";
import { rememberBrowserPreference } from "../browserPreferences.ts";
import GoogleMark from "../GoogleMark";
import type { Device } from "./cloud";
import desktopReportEn from "./media/desktop-report-en.webp";
import desktopReportZh from "./media/desktop-report-zh.webp";
import phoneMarketEn from "./media/phone-market-en.webp";
import phoneMarketZh from "./media/phone-market-zh.webp";
import phoneReportEn from "./media/phone-report-en.webp";
import phoneReportZh from "./media/phone-report-zh.webp";
import "./Gate.css";

const english = () => uiLocale() === "en-US";
const media = () => english()
  ? { desktop: desktopReportEn, phone: phoneReportEn, market: phoneMarketEn }
  : { desktop: desktopReportZh, phone: phoneReportZh, market: phoneMarketZh };
/** txintrade.com in the page's language. */
const siteUrl = (path = "") => `https://txintrade.com${english() ? "" : "/zh-TW"}${path}`;

type GlyphName = "check" | "lock" | "cpu" | "relay" | "phone" | "download" | "google" | "toggle"
  | "moon" | "power" | "warning" | "arrow" | "refresh" | "spark" | "shield" | "globe";
const GLYPHS: Record<GlyphName, string> = {
  check: "M5 12.5l4.2 4.2L19 7",
  lock: "M7 10.5V8a5 5 0 0 1 10 0v2.5M5.5 10.5h13v9.5h-13zM12 14.5v2",
  cpu: "M4 5.5h16v10.5H4zM2.5 19h19M9.5 16v3M14.5 16v3",
  relay: "M5 12a7 7 0 0 1 14 0M8.5 12a3.5 3.5 0 0 1 7 0M12 12v.01",
  phone: "M8 3h8a1.5 1.5 0 0 1 1.5 1.5v15A1.5 1.5 0 0 1 16 21H8a1.5 1.5 0 0 1-1.5-1.5v-15A1.5 1.5 0 0 1 8 3zM11 18h2",
  download: "M12 4v11M7.5 10.5 12 15l4.5-4.5M5 19.5h14",
  google: "M12 12.5a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7zM5 20a7 7 0 0 1 14 0",
  toggle: "M7.5 7.5h9a4.5 4.5 0 0 1 0 9h-9a4.5 4.5 0 0 1 0-9zM16.5 14a2 2 0 1 0 0-4 2 2 0 0 0 0 4z",
  moon: "M19.5 14.5A8 8 0 0 1 9.5 4.5a8 8 0 1 0 10 10z",
  power: "M12 3.5v8M7 6.5a7 7 0 1 0 10 0",
  warning: "M12 4 2.8 20h18.4zM12 10v4.5M12 17.5v.01",
  arrow: "M5 12h14M13.5 6.5 19 12l-5.5 5.5",
  refresh: "M19.5 8.5A8 8 0 0 0 5 7.5M4.5 4v3.5H8M4.5 15.5A8 8 0 0 0 19 16.5M19.5 20v-3.5H16",
  spark: "M12 3v4M12 17v4M3 12h4M17 12h4M6 6l2.5 2.5M15.5 15.5 18 18M18 6l-2.5 2.5M8.5 15.5 6 18",
  shield: "M12 3.5 5 6.5v5c0 4.2 3 7.6 7 9 4-1.4 7-4.8 7-9v-5z M9 12l2 2 4-4",
  globe: "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM3.5 12h17M12 3c2.3 2.4 3.5 5.4 3.5 9s-1.2 6.6-3.5 9c-2.3-2.4-3.5-5.4-3.5-9S9.7 5.4 12 3z",
};

export function Glyph({ name, className }: { name: GlyphName; className?: string }) {
  return (
    <svg className={`gate-glyph${className ? ` ${className}` : ""}`} viewBox="0 0 24 24" aria-hidden="true"
      fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
      <path d={GLYPHS[name]} />
    </svg>
  );
}

/** Switches between the two languages; the choice stays in this browser only. */
function LanguageSwitch() {
  const next = uiLocale() === "en-US" ? "zh-TW" : "en-US";
  return (
    <button type="button" className="gate-bar-link gate-language" lang={next} aria-label={uiText("切換語言")}
      onClick={() => { rememberBrowserPreference("locale", next); void setUiLocale(next); }}>
      <Glyph name="globe" /><span>{languageName(next)}</span>
    </button>
  );
}

/** Page frame: brand bar, content, and the links every page of txinTrade carries. */
export function GateShell({ account, children, tone }: { account?: ReactNode; children: ReactNode; tone?: "hero" }) {
  useEffect(() => {
    setThemeSurface("gate");
    return () => setThemeSurface("app");
  }, []);
  return (
    <div className={`gate${tone ? ` gate-tone-${tone}` : ""}`}>
      <div className="gate-aura" aria-hidden="true" />
      <header className="gate-bar">
        <a className="gate-wordmark" href={siteUrl("/")} aria-label="txinTrade">txin<strong>Trade</strong></a>
        <div className="gate-bar-end">
          <LanguageSwitch />
          {account ?? (
            <a className="gate-bar-link" href={siteUrl("/download")}>{uiText("下載 App")}<Glyph name="arrow" /></a>
          )}
        </div>
      </header>
      <main className="gate-main">{children}</main>
      <footer className="gate-foot">
        <span>© {new Date().getFullYear()} txinTrade</span>
        <nav aria-label="txinTrade">
          <a href={siteUrl("/")}>{uiText("官網")}</a>
          <a href={siteUrl("/pricing")}>{uiText("價格")}</a>
          <a href={siteUrl("/privacy")}>{uiText("隱私權政策")}</a>
          <a href={siteUrl("/terms")}>{uiText("服務條款")}</a>
          <a href={siteUrl("/contact")}>{uiText("聯絡我們")}</a>
        </nav>
      </footer>
    </div>
  );
}

/** A phone showing a real txinTrade screen. */
function PhoneFrame({ src, className }: { src: string; className?: string }) {
  return (
    <figure className={`gate-phone${className ? ` ${className}` : ""}`}>
      <div className="gate-phone-screen">
        <span className="gate-phone-status"><b>9:41</b><i /></span>
        <img src={src} alt="" width={554} height={1200} loading="eager" decoding="async" />
      </div>
    </figure>
  );
}

/** Your computer and your phone, joined by the relay: what remote access is. */
function DeviceDuo() {
  const { desktop, phone } = media();
  return (
    <div className="gate-duo" aria-hidden="true">
      <div className="gate-window">
        <div className="gate-window-bar"><i /><i /><i /><span>txinTrade</span></div>
        <img src={desktop} alt="" width={1280} height={800} decoding="async" />
      </div>
      <span className="gate-link-badge"><i />{uiText("加密轉送")}<Glyph name="lock" /></span>
      <PhoneFrame src={phone} className="gate-duo-phone" />
    </div>
  );
}

const TRUST = [
  ["cpu", "分析在你的電腦上執行"],
  ["shield", "金鑰不離開電腦"],
  ["spark", "只分析，不下單"],
] as const;

/** Signed out: what this page is, and Google sign-in. */
export function Welcome({ signInHref }: { signInHref: string }) {
  return (
    <>
      <section className="gate-hero">
        <div className="gate-hero-copy">
          <p className="gate-eyebrow rise" style={{ "--d": "0s" } as CSSProperties}>{uiText("txinTrade 遠端存取")}</p>
          <h1 className="gate-display rise" style={{ "--d": "0.06s" } as CSSProperties}>
            <span className="gate-line">{uiText("你的交易分析，")}</span>
            <span className="gate-line gate-shine">{uiText("隨身帶著走。")}</span>
          </h1>
          <p className="gate-lead rise" style={{ "--d": "0.14s" } as CSSProperties}>
            {uiText("查看電腦上的分析與持倉，或遠端發起新的分析。分析仍在你的電腦上執行，金鑰與完整資料不會離開電腦。")}
          </p>
          <div className="gate-actions rise" style={{ "--d": "0.22s" } as CSSProperties}>
            <a className="gate-button gate-google" href={signInHref}><GoogleMark /><span>{uiText("使用 Google 登入")}</span></a>
            <a className="gate-text-link" href={siteUrl("/download")}>{uiText("還沒有 App？免費下載")}<Glyph name="arrow" /></a>
          </div>
          <ul className="gate-trust rise" style={{ "--d": "0.3s" } as CSSProperties}>
            {TRUST.map(([icon, text]) => <li key={text}><Glyph name={icon} />{uiText(text)}</li>)}
          </ul>
        </div>
        <DeviceDuo />
      </section>
      <section className="gate-how" aria-labelledby="gate-how-title">
        <h2 id="gate-how-title" className="gate-section-title">{uiText("運作方式")}</h2>
        <ol>
          <li>
            <span className="gate-how-icon"><Glyph name="cpu" /></span>
            <strong>{uiText("電腦負責分析")}</strong>
            <span>{uiText("txinTrade 在你的電腦上，用你自己的 Claude 或 ChatGPT 方案分析行情與持倉。")}</span>
          </li>
          <li>
            <span className="gate-how-icon"><Glyph name="relay" /></span>
            <strong>{uiText("雲端只負責轉送")}</strong>
            <span>{uiText("電腦主動連到 txinTrade 雲端，不開放任何連入的通訊埠；金鑰與完整資料留在電腦上。")}</span>
          </li>
          <li>
            <span className="gate-how-icon"><Glyph name="phone" /></span>
            <strong>{uiText("隨時隨地使用")}</strong>
            <span>{uiText("在手機或任何瀏覽器查看分析、發起新的分析，並即時追問 AI。")}</span>
          </li>
        </ol>
      </section>
    </>
  );
}

/** A computer waiting to appear: rings that keep listening while the page polls. */
function Listening({ offline }: { offline?: boolean }) {
  return (
    <div className={`gate-listen${offline ? " is-offline" : ""}`} aria-hidden="true">
      <span /><span /><span />
      <div className="gate-listen-core"><Glyph name={offline ? "moon" : "cpu"} /></div>
    </div>
  );
}

/** Signed in, no computer has ever linked: set it up on the desktop. */
export function Setup({ email, onRefresh }: { email: string | null; onRefresh: () => void }) {
  const steps: Array<{ icon: GlyphName; title: string; body: ReactNode }> = [
    {
      icon: "download",
      title: uiText("在電腦上開啟 txinTrade"),
      body: <>{uiText("還沒安裝？")}{" "}<a href={siteUrl("/download")}>{uiText("下載 txinTrade")}</a></>,
    },
    {
      icon: "google",
      title: uiText("用同一個 Google 帳號登入"),
      body: email
        ? uiText("點左下角的帳戶，選「登入雲端帳戶以遠端使用」，用 {{p0}} 登入。", { p0: email })
        : uiText("點左下角的帳戶，選「登入雲端帳戶以遠端使用」，用同一個帳號登入。"),
    },
    {
      icon: "toggle",
      title: uiText("打開「遠端存取」"),
      body: uiText("在同一個帳戶選單打開「遠端存取」，並讓 txinTrade 保持開啟。這個頁面會自動連上你的電腦。"),
    },
  ];
  return (
    <section className="gate-split">
      <div className="gate-split-copy">
        <p className="gate-eyebrow">{uiText("開始使用")}</p>
        <h1 className="gate-title">{uiText("先連上你的電腦")}</h1>
        <p className="gate-lead">{email
          ? uiText("你登入的是 {{p0}}，這個帳號還沒有連線過任何電腦。遠端網頁使用的是你電腦上的 txinTrade，請先在電腦上完成以下設定：", { p0: email })
          : uiText("這個帳號還沒有連線過任何電腦。遠端網頁使用的是你電腦上的 txinTrade，請先在電腦上完成以下設定：")}</p>
        <ol className="gate-steps">
          {steps.map((step, index) => (
            <li key={step.title}>
              <span className="gate-step-icon"><Glyph name={step.icon} /><b>{index + 1}</b></span>
              <span className="gate-step-copy"><strong>{step.title}</strong><span>{step.body}</span></span>
            </li>
          ))}
        </ol>
      </div>
      <aside className="gate-wait" aria-live="polite">
        <Listening />
        <strong>{uiText("正在等待你的電腦")}</strong>
        <span>{uiText("完成設定後，這個頁面會自動連上，不需要重新整理。")}</span>
        <button type="button" className="gate-button gate-quiet" onClick={onRefresh}><Glyph name="refresh" />{uiText("立即檢查")}</button>
      </aside>
    </section>
  );
}

/** A linked computer that is not online now. */
export function Offline({ device, devices, onChoose, onRefresh }: {
  device: Device;
  devices: Device[];
  onChoose: (id: string) => void;
  onRefresh: () => void;
}) {
  const checks = [
    ["power", "電腦已開機，而且沒有進入睡眠"],
    ["cpu", "txinTrade 正在電腦上執行"],
    ["toggle", "帳戶選單中的「遠端存取」已打開"],
  ] as const;
  return (
    <section className="gate-center">
      <Listening offline />
      <p className="gate-device-chip is-offline"><i />{device.label}<em>{uiText("離線")}</em></p>
      <h1 className="gate-title">{uiText("電腦目前離線")}</h1>
      <p className="gate-lead">{uiText("遠端網頁使用的是你電腦上的 txinTrade。請確認：")}</p>
      <ul className="gate-checks">
        {checks.map(([icon, text]) => <li key={text}><Glyph name={icon} />{uiText(text)}</li>)}
      </ul>
      <div className="gate-actions">
        <button type="button" className="gate-button" onClick={onRefresh}><Glyph name="refresh" />{uiText("立即檢查")}</button>
        {devices.length > 1 && (
          <label className="gate-select">
            <span className="visually-hidden">{uiText("選擇電腦")}</span>
            <select value={device.id} onChange={(event) => onChoose(event.target.value)}>
              {devices.map((item) => <option key={item.id} value={item.id}>{item.label}</option>)}
            </select>
          </label>
        )}
      </div>
      <p className="gate-hint">{uiText("電腦一連上，這個頁面就會自動進入。")}</p>
    </section>
  );
}

const FEATURES = [
  "完整的電腦版畫面：市場、持倉、事件與紀錄",
  "遠端發起市場與持倉分析",
  "即時串流的 AI 追問",
  "最多可連結 2 台電腦",
] as const;

/** The computer is linked and online; only the subscription is missing. */
export function Subscribe({ computer, busy, error, onSubscribe, onManage }: {
  computer: string;
  busy: boolean;
  error: string;
  onSubscribe: () => void;
  onManage?: () => void;
}) {
  return (
    <section className="gate-offer">
      <div className="gate-offer-copy">
        <p className="gate-device-chip"><i />{uiText("{{p0}} 已連線", { p0: computer })}</p>
        <h1 className="gate-title gate-title-lg">
          <span className="gate-line">{uiText("最後一步，")}</span>
          <span className="gate-line gate-shine">{uiText("隨處使用你的電腦。")}</span>
        </h1>
        <p className="gate-lead">{uiText("從任何瀏覽器使用電腦上的 txinTrade：查看分析與持倉、遠端發起分析、即時追問。分析仍在你的電腦上執行。")}</p>
        <ul className="gate-trust">
          {TRUST.map(([icon, text]) => <li key={text}><Glyph name={icon} />{uiText(text)}</li>)}
        </ul>
      </div>
      <div className="gate-offer-side">
      <div className="gate-offer-art" aria-hidden="true"><PhoneFrame src={media().market} /></div>
      <div className="gate-price-card">
        <div className="gate-price-head">
          <span className="gate-price-name">{uiText("遠端存取")}</span>
          <span className="gate-price"><b>US$3.99</b><small>{uiText("／月")}</small></span>
          <span className="gate-price-note">{uiText("以美元按月計費，隨時可取消。")}</span>
        </div>
        <ul className="gate-features">
          {FEATURES.map((text) => <li key={text}><Glyph name="check" />{uiText(text)}</li>)}
        </ul>
        {error && <p className="gate-error" role="alert"><Glyph name="warning" />{error}</p>}
        <button type="button" className="gate-button gate-wide" disabled={busy} aria-busy={busy} onClick={onSubscribe}>
          {busy ? <><span className="gate-spin" aria-hidden="true" />{uiText("正在前往付款頁…")}</> : <>{uiText("訂閱遠端存取")}<Glyph name="arrow" /></>}
        </button>
        <p className="gate-secure"><Glyph name="lock" />{uiText("透過 Creem 安全結帳")}</p>
        <p className="gate-fine">{uiText("隨時取消 · 首次付款 7 天內可全額退款")}</p>
        {onManage && (
          <button type="button" className="gate-text-link gate-manage" onClick={onManage}>{uiText("管理訂閱")}<Glyph name="arrow" /></button>
        )}
      </div>
      </div>
    </section>
  );
}

/** Back from checkout: payment is done, access opens when the webhook lands. */
export function Confirming() {
  const steps = [
    ["付款完成", "done"],
    ["開通遠端存取", "active"],
    ["連上你的電腦", "next"],
  ] as const;
  return (
    <section className="gate-center" aria-live="polite">
      <div className="gate-orbit" aria-hidden="true"><Glyph name="lock" /></div>
      <h1 className="gate-title">{uiText("正在確認付款")}</h1>
      <p className="gate-lead">{uiText("通常只需要幾秒鐘，請不要關閉這個頁面。")}</p>
      <ol className="gate-progress">
        {steps.map(([text, state]) => (
          <li key={text} data-state={state}>
            <span className="gate-progress-mark">{state === "done" ? <Glyph name="check" /> : state === "active" ? <span className="gate-spin" /> : null}</span>
            {uiText(text)}
          </li>
        ))}
      </ol>
    </section>
  );
}

/** Waiting on the cloud or the computer. */
export function Waiting({ label }: { label: string }) {
  return (
    <section className="gate-center gate-waiting" role="status">
      <div className="gate-orbit is-idle" aria-hidden="true"><img className="gate-orbit-icon" src="/icon-192.png" alt="" width={64} height={64} /></div>
      <p className="gate-waiting-label">{label}</p>
    </section>
  );
}

/** Something stopped the workspace from opening; say what and offer a retry. */
export function Problem({ title, message, action, onAction, update }: {
  title: string;
  message: string;
  action: string;
  onAction: () => void;
  /** Offer the download page, for an app too old for remote screens. */
  update?: boolean;
}) {
  return (
    <section className="gate-center">
      <div className="gate-problem-icon" aria-hidden="true"><Glyph name="warning" /></div>
      <h1 className="gate-title">{title}</h1>
      <p className="gate-lead" role="alert">{message}</p>
      <div className="gate-actions">
        {update && <a className="gate-button" href={siteUrl("/download")}><Glyph name="download" />{uiText("下載最新版")}</a>}
        <button type="button" className={`gate-button${update ? " gate-quiet" : ""}`} onClick={onAction}><Glyph name="refresh" />{action}</button>
      </div>
    </section>
  );
}

/** A page-level message above the current screen. */
export function Notice({ children }: { children: ReactNode }) {
  return <p className="gate-notice" role="alert"><Glyph name="warning" />{children}</p>;
}
