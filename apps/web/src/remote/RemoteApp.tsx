import { useCallback, useEffect, useState, type ReactNode } from "react";
import { setUiLocale, uiLocale, uiText, useUiLocale, type UiLocale } from "../i18n/index.ts";
import { AnalysisSpinner } from "../AnalysisProgress";
import DirectionAssessment, { type DirectionAssessmentData } from "../DirectionAssessment";
import GoogleMark from "../GoogleMark";
import { CloudError, listDevices, readMe, runCommand, signInUrl, signOut, type Device, type Me } from "./cloud";
import "./RemoteApp.css";

type Stance = "long" | "short" | "wait" | null;
type EntryDecision = {
  action: "open_now" | "wait_for_entry" | "stand_aside";
  side: "long" | "short" | null;
  entry_price?: string | null;
  stop_loss?: string | null;
  take_profit?: string | null;
  trigger?: string | null;
  invalidation?: string | null;
  reason?: string;
};
type AnalysisSummary = {
  id: string;
  status: string;
  kind: string | null;
  market_id: string | null;
  timeframe: string | null;
  created_at: string | null;
  completed_at: string | null;
  agent_stance: Stance;
  entry_action: EntryDecision["action"] | null;
};
type AnalysisDetail = AnalysisSummary & {
  error: unknown;
  report: {
    generated_at: string | null;
    agent_stance: Stance;
    entry_decision: EntryDecision | null;
    reasoning: Partial<Record<"market" | "levels" | "strategy" | "supporting_evidence" | "counter_evidence", string | null>> & {
      direction_assessment?: DirectionAssessmentData;
    };
    quote: { price: string | null; observed_at: string | null };
  } | null;
};
type Position = {
  id: string;
  market_id: string;
  side: "long" | "short";
  leverage: number;
  entry_price: string;
  quantity: string;
  mark_price?: string | null;
  unrealized_profit?: string | null;
};
type Tab = "analyses" | "start" | "positions";

const DEVICE_KEY = "txintrade.remote.device";
const TIMEFRAMES = ["1h", "4h", "12h", "1d"] as const;
const QUICK_SYMBOLS = ["BTC", "ETH", "SOL"];

function remember(key: string, value?: string): string | null {
  try {
    if (value !== undefined) window.localStorage.setItem(key, value);
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

function errorText(error: unknown): string {
  const code = error instanceof CloudError ? error.code : "";
  switch (code) {
    case "device_offline": return uiText("電腦目前離線。請確認電腦開著 txinTrade，且已打開手機遠端存取。");
    case "subscription_required": return uiText("遠端存取需要有效的訂閱方案。");
    case "local_unavailable": return uiText("電腦上找不到這筆資料，或 App 正在更新。");
    case "unsupported_operation": return uiText("電腦上的 txinTrade 版本不支援這個操作，請更新 App。");
    case "expired": return uiText("電腦沒有及時回應，請重試。");
    case "network": return uiText("無法連線到 txinTrade 雲端，請檢查網路後重試。");
    default: return uiText("操作未完成，請稍後重試。");
  }
}

const symbolOf = (marketId: string | null) => (marketId ?? "").replace("binance:perp:", "");

function formatTime(value: string | null): string {
  if (!value) return "";
  return new Date(value).toLocaleString(uiLocale(), { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false });
}

function stanceText(stance: Stance): string {
  return stance === "long" ? uiText("偏多") : stance === "short" ? uiText("偏空") : uiText("觀望");
}

function actionText(action: EntryDecision["action"] | null, side: EntryDecision["side"] = null): string {
  const direction = side === "long" ? uiText("做多") : side === "short" ? uiText("做空") : uiText("進場");
  if (action === "open_now") return uiText("可以{{p0}}", { p0: direction });
  if (action === "wait_for_entry") return uiText("等待{{p0}}條件", { p0: direction });
  if (action === "stand_aside") return uiText("觀望");
  return uiText("AI 市場判斷");
}

function StancePill({ stance }: { stance: Stance }) {
  if (!stance) return null;
  return <span className={`stance-pill ${stance}`}>{stanceText(stance)}</span>;
}

function Notice({ children, tone = "info" }: { children: ReactNode; tone?: "info" | "warn" }) {
  return <p className={`remote-notice ${tone}`} role={tone === "warn" ? "alert" : "status"}>{children}</p>;
}

function Loading({ label }: { label: string }) {
  return <p className="remote-loading" role="status"><AnalysisSpinner />{label}</p>;
}

function AnalysesTab({ device, onOpen }: { device: Device; onOpen: (id: string) => void }) {
  const [items, setItems] = useState<AnalysisSummary[] | null>(null);
  const [error, setError] = useState("");
  const load = useCallback(() => {
    runCommand<{ analyses: AnalysisSummary[] }>(device.id, { operation: "analyses.list", limit: 20 })
      .then((value) => setItems(value.analyses))
      .catch((reason) => setError(errorText(reason)));
  }, [device.id]);
  useEffect(load, [load]);
  if (error) return <><Notice tone="warn">{error}</Notice><button type="button" className="remote-secondary" onClick={() => { setError(""); load(); }}>{uiText("重試")}</button></>;
  if (!items) return <Loading label={uiText("正在從電腦讀取分析…")} />;
  if (!items.length) return <Notice>{uiText("這台電腦還沒有分析紀錄。")}</Notice>;
  return (
    <ul className="remote-list">
      {items.map((item) => (
        <li key={item.id}>
          <button type="button" className="remote-row" onClick={() => onOpen(item.id)}>
            <span className="remote-row-main">
              <strong>{symbolOf(item.market_id)}<small>{item.timeframe}</small></strong>
              <span>{item.status === "completed" ? actionText(item.entry_action)
                : item.status === "failed" ? uiText("分析失敗") : uiText("分析中…")}</span>
            </span>
            <span className="remote-row-side">
              <StancePill stance={item.status === "completed" ? item.agent_stance : null} />
              <time>{formatTime(item.completed_at ?? item.created_at)}</time>
            </span>
          </button>
        </li>
      ))}
    </ul>
  );
}

function AnalysisView({ device, id, onBack }: { device: Device; id: string; onBack: () => void }) {
  const [detail, setDetail] = useState<AnalysisDetail | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    let timer = 0;
    const load = () => runCommand<{ analysis: AnalysisDetail }>(device.id, { operation: "analyses.get", analysis_id: id })
      .then(({ analysis }) => {
        if (!active) return;
        setDetail(analysis);
        // A queued or running analysis finishes on the computer; check again shortly.
        if (analysis.status === "queued" || analysis.status === "running") timer = window.setTimeout(load, 5000);
      })
      .catch((reason) => { if (active) setError(errorText(reason)); });
    void load();
    return () => { active = false; window.clearTimeout(timer); };
  }, [device.id, id]);
  const report = detail?.report;
  const entry = report?.entry_decision;
  const reasoning = report?.reasoning ?? {};
  const sections: Array<[string, string | null | undefined]> = [
    [uiText("市場狀態"), reasoning.market],
    [uiText("關鍵價位"), reasoning.levels],
    [uiText("策略"), reasoning.strategy],
    [uiText("支持證據"), reasoning.supporting_evidence],
    [uiText("反向證據"), reasoning.counter_evidence],
  ];
  return (
    <article className="remote-detail">
      <button type="button" className="remote-back" onClick={onBack}>‹ {uiText("返回")}</button>
      {error ? <Notice tone="warn">{error}</Notice> : !detail ? <Loading label={uiText("正在從電腦讀取報告…")} /> : (
        <>
          <header className="remote-detail-head">
            <h2>{symbolOf(detail.market_id)} <small>{detail.timeframe}</small></h2>
            <time>{formatTime(report?.generated_at ?? detail.created_at)}</time>
          </header>
          {detail.status === "queued" || detail.status === "running" ? (
            <Loading label={uiText("電腦正在分析，完成後會自動顯示。")} />
          ) : !report ? (
            <Notice tone="warn">{uiText("這次分析沒有完成。可以在電腦上查看原因。")}</Notice>
          ) : (
            <>
              <section className="panel remote-decision">
                <StancePill stance={report.agent_stance} />
                <h3>{actionText(entry?.action ?? null, entry?.side ?? null)}</h3>
                {entry?.reason && <p>{entry.reason}</p>}
                <dl className="remote-prices">
                  {report.quote.price && <><dt>{uiText("現價")}</dt><dd>{report.quote.price}</dd></>}
                  {entry?.entry_price && <><dt>{uiText("進場價")}</dt><dd>{entry.entry_price}</dd></>}
                  {entry?.stop_loss && <><dt>{uiText("止損")}</dt><dd>{entry.stop_loss}</dd></>}
                  {entry?.take_profit && <><dt>{uiText("止盈")}</dt><dd>{entry.take_profit}</dd></>}
                </dl>
                {entry?.trigger && <p className="remote-condition"><b>{uiText("觸發條件")}</b>{entry.trigger}</p>}
                {entry?.invalidation && <p className="remote-condition"><b>{uiText("失效條件")}</b>{entry.invalidation}</p>}
              </section>
              <DirectionAssessment assessment={reasoning.direction_assessment} />
              {sections.filter(([, text]) => text).map(([title, text]) => (
                <section key={title} className="panel remote-reasoning">
                  <h3>{title}</h3>
                  <p>{text}</p>
                </section>
              ))}
              <p className="remote-footnote">{uiText("手機只顯示報告重點；完整圖表與證據請在電腦上查看。")}</p>
            </>
          )}
        </>
      )}
    </article>
  );
}

function StartTab({ device, onStarted }: { device: Device; onStarted: (id: string) => void }) {
  const [symbol, setSymbol] = useState("BTC");
  const [timeframe, setTimeframe] = useState<(typeof TIMEFRAMES)[number]>("1h");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const normalized = symbol.trim().toUpperCase().replace(/USDT$/, "");
  const valid = /^[A-Z0-9]{2,30}$/.test(normalized);
  const start = async () => {
    setBusy(true);
    setError("");
    try {
      const result = await runCommand<{ analysis_id: string }>(device.id, {
        operation: "analyses.start", market_id: `binance:perp:${normalized}USDT`, timeframe, output_locale: uiLocale(),
      });
      onStarted(result.analysis_id);
    } catch (reason) {
      setError(errorText(reason));
    } finally {
      setBusy(false);
    }
  };
  return (
    <form className="remote-start" onSubmit={(event) => { event.preventDefault(); if (valid && !busy) void start(); }}>
      <label className="remote-field">
        <span>{uiText("交易對（Binance USDT 永續）")}</span>
        <span className="remote-symbol-input">
          <input value={symbol} onChange={(event) => setSymbol(event.target.value)} autoCapitalize="characters"
            autoCorrect="off" spellCheck={false} inputMode="text" aria-invalid={!valid} />
          <span aria-hidden="true">USDT</span>
        </span>
      </label>
      <div className="remote-chips" aria-label={uiText("常用交易對")}>
        {QUICK_SYMBOLS.map((item) => (
          <button type="button" key={item} className={normalized === item ? "chosen" : ""} onClick={() => setSymbol(item)}>{item}</button>
        ))}
      </div>
      <fieldset className="remote-field">
        <legend>{uiText("週期")}</legend>
        <div className="segments remote-segments">
          {TIMEFRAMES.map((item) => (
            <button type="button" key={item} className={timeframe === item ? "chosen" : ""} aria-pressed={timeframe === item}
              onClick={() => setTimeframe(item)}>{item}</button>
          ))}
        </div>
      </fieldset>
      <p className="remote-footnote">{uiText("分析在你的電腦上執行，並沿用電腦上儲存的交易偏好與 AI 帳號。")}</p>
      {error && <Notice tone="warn">{error}</Notice>}
      <button type="submit" className="action remote-primary" disabled={!valid || busy}>
        {busy ? <><AnalysisSpinner />{uiText("正在送到電腦…")}</> : uiText("在電腦上開始分析")}
      </button>
    </form>
  );
}

function PositionsTab({ device }: { device: Device }) {
  const [items, setItems] = useState<Position[] | null>(null);
  const [error, setError] = useState("");
  const load = useCallback(() => {
    runCommand<{ positions: Position[] }>(device.id, { operation: "positions.list" })
      .then((value) => setItems(value.positions))
      .catch((reason) => setError(errorText(reason)));
  }, [device.id]);
  useEffect(load, [load]);
  if (error) return <><Notice tone="warn">{error}</Notice><button type="button" className="remote-secondary" onClick={() => { setError(""); load(); }}>{uiText("重試")}</button></>;
  if (!items) return <Loading label={uiText("正在從電腦讀取持倉…")} />;
  if (!items.length) return <Notice>{uiText("這台電腦目前沒有持倉。")}</Notice>;
  return (
    <ul className="remote-list">
      {items.map((item) => {
        const pnl = item.unrealized_profit ? Number(item.unrealized_profit) : null;
        return (
          <li key={item.id} className="remote-row static">
            <span className="remote-row-main">
              <strong>{symbolOf(item.market_id)}
                <span className={`stance-pill ${item.side}`}>{item.side === "long" ? uiText("做多") : uiText("做空")} {item.leverage}x</span>
              </strong>
              <span>{uiText("進場 {{p0}} · 數量 {{p1}}", { p0: item.entry_price, p1: item.quantity })}</span>
            </span>
            <span className="remote-row-side">
              {pnl !== null && Number.isFinite(pnl) && (
                <b className={pnl >= 0 ? "positive" : "negative"}>{pnl >= 0 ? "+" : ""}{item.unrealized_profit}</b>
              )}
              {item.mark_price && <small>{uiText("標記 {{p0}}", { p0: item.mark_price })}</small>}
            </span>
          </li>
        );
      })}
    </ul>
  );
}

/** Phone page for the txinTrade cloud: reach your own computer through the relay. */
export default function RemoteApp() {
  const locale = useUiLocale();
  const [me, setMe] = useState<Me | null | undefined>(undefined);
  const [subscribed, setSubscribed] = useState(false);
  const [devices, setDevices] = useState<Device[] | null>(null);
  const [deviceId, setDeviceId] = useState<string | null>(() => remember(DEVICE_KEY));
  const [tab, setTab] = useState<Tab>("analyses");
  const [openId, setOpenId] = useState<string | null>(null);
  const [error, setError] = useState(() => new URLSearchParams(window.location.search).get("sign_in_error")
    ? uiText("登入未完成，請重試。") : "");

  const refreshDevices = useCallback(() => listDevices().then(setDevices).catch((reason) => setError(errorText(reason))), []);

  useEffect(() => {
    if (window.location.search) window.history.replaceState(null, "", window.location.pathname);
    readMe().then((value) => {
      setSubscribed(value.entitlements.some((item) => item.feature === "remote_access" && item.status === "active"
        && (item.expires_at === null || item.expires_at > Date.now())));
      setMe(value);
      void refreshDevices();
    })
      .catch((reason) => {
        if (reason instanceof CloudError && reason.status === 401) setMe(null);
        else { setMe(null); setError(errorText(reason)); }
      });
  }, [refreshDevices]);

  // Coming back to the page (for example after waking the computer) re-checks who is online.
  useEffect(() => {
    if (!me) return;
    const check = () => { if (document.visibilityState === "visible") void refreshDevices(); };
    document.addEventListener("visibilitychange", check);
    const timer = window.setInterval(check, 15000);
    return () => { document.removeEventListener("visibilitychange", check); window.clearInterval(timer); };
  }, [me, refreshDevices]);

  const device = devices?.find((item) => item.id === deviceId) ?? devices?.find((item) => item.online) ?? devices?.[0] ?? null;
  const name = me?.profile?.display_name || me?.profile?.email || "";
  const chooseDevice = (id: string) => { setDeviceId(id); remember(DEVICE_KEY, id); setOpenId(null); };
  const toggleLocale = () => void setUiLocale((locale === "zh-TW" ? "en-US" : "zh-TW") as UiLocale);

  return (
    <div className="remote-shell">
      <header className="remote-top">
        <span className="brand-wordmark">txin<strong>Trade</strong></span>
        <span className="remote-top-actions">
          <button type="button" className="remote-text-button" onClick={toggleLocale}>{locale === "zh-TW" ? "EN" : "中文"}</button>
          {me && (
            <button type="button" className="remote-text-button" title={me.profile?.email ?? undefined}
              onClick={() => void signOut().finally(() => { setMe(null); setDevices(null); })}>
              <span className="avatar" aria-hidden="true">{Array.from(name || "?")[0].toUpperCase()}</span>
              {uiText("登出")}
            </button>
          )}
        </span>
      </header>
      <main className="remote-main">
        {error && <Notice tone="warn">{error}</Notice>}
        {me === undefined ? <Loading label={uiText("正在連線到 txinTrade 雲端…")} /> : me === null ? (
          <section className="remote-welcome">
            <h1>{uiText("在手機上使用你的 txinTrade")}</h1>
            <p>{uiText("查看電腦上的分析與持倉，或遠端發起新的分析。分析仍在你的電腦上執行，金鑰與完整資料不會離開電腦。")}</p>
            <a className="cloud-google-button" href={signInUrl()}><GoogleMark /><span>{uiText("使用 Google 登入")}</span></a>
          </section>
        ) : !subscribed ? (
          <Notice tone="warn">{uiText("遠端存取需要有效的訂閱方案。")}</Notice>
        ) : !devices ? <Loading label={uiText("正在讀取你的電腦…")} /> : !device ? (
          <section className="remote-welcome">
            <h2>{uiText("還沒有可連線的電腦")}</h2>
            <p>{uiText("在電腦版 txinTrade 的「設定 → 雲端帳戶」登入同一個 Google 帳號，並打開「手機遠端存取」。")}</p>
            <button type="button" className="remote-secondary" onClick={() => void refreshDevices()}>{uiText("重新整理")}</button>
          </section>
        ) : (
          <>
            <section className="remote-device" aria-label={uiText("電腦")}>
              <span className={`remote-device-dot${device.online ? " online" : ""}`} aria-hidden="true" />
              <span className="remote-device-copy">
                {devices.length > 1 ? (
                  <select value={device.id} onChange={(event) => chooseDevice(event.target.value)} aria-label={uiText("選擇電腦")}>
                    {devices.map((item) => <option key={item.id} value={item.id}>{item.label}</option>)}
                  </select>
                ) : <strong>{device.label}</strong>}
                <small>{device.online ? uiText("在線") : uiText("離線：請在電腦上開啟 txinTrade")}</small>
              </span>
              <button type="button" className="remote-text-button" onClick={() => void refreshDevices()}>{uiText("重新整理")}</button>
            </section>
            {!device.online ? (
              <Notice>{uiText("電腦需要開著 txinTrade 並打開「手機遠端存取」；電腦睡眠時也無法連線。")}</Notice>
            ) : openId ? (
              <AnalysisView key={`${device.id}:${openId}`} device={device} id={openId} onBack={() => setOpenId(null)} />
            ) : (
              <>
                <nav className="segments remote-tabs" aria-label={uiText("遠端功能")}>
                  {([["analyses", uiText("分析")], ["start", uiText("發起分析")], ["positions", uiText("持倉")]] as const).map(([id, label]) => (
                    <button type="button" key={id} className={tab === id ? "chosen" : ""} aria-pressed={tab === id}
                      onClick={() => setTab(id)}>{label}</button>
                  ))}
                </nav>
                {tab === "analyses" && <AnalysesTab key={device.id} device={device} onOpen={setOpenId} />}
                {tab === "start" && <StartTab device={device} onStarted={(id) => { setTab("analyses"); setOpenId(id); }} />}
                {tab === "positions" && <PositionsTab key={device.id} device={device} />}
              </>
            )}
          </>
        )}
      </main>
    </div>
  );
}
