import { useEffect, useMemo, useState } from "react";
import Icon from "./Icon";
import { uiText, type UiLocale } from "./i18n/index.ts";
import { apiFetch } from "./transport";
import {
  assetForMarket,
  explorerUrl,
  FLOW_WINDOWS,
  formatAmount,
  formatUsd,
  lastSynced,
  seriesScale,
  shortAddress,
  SYNC_LATE_MS,
  type Coverage,
  type FlowEvent,
  type FlowSeries,
  type FlowTotals,
  type FlowWindow,
  type SmartMoneyAsset,
  type SmartMoneyOverview,
  type TrackedAsset,
} from "./smartMoney";
import "./SmartMoneyPanel.css";

const REFRESH_MS = 60_000;

async function load<T>(path: string, signal?: AbortSignal): Promise<T> {
  const response = await apiFetch(`/api/v1/smart-money${path}`, { signal });
  if (!response.ok) throw new Error(String(response.status));
  return response.json();
}

const windowLabel = (w: FlowWindow) =>
  ({ "1d": uiText("24 小時"), "7d": uiText("7 天"), "30d": uiText("30 天"), "90d": uiText("90 天") })[w];

function coverageNote(coverage: Coverage, asset: string): string {
  if (coverage === "large_only")
    return uiText("BTC 只檢查 50 BTC 以上的交易，交易所錢包標籤也仍在累積，流入流出會偏低。");
  if (coverage === "exchange_and_whales")
    return uiText("包含所有進出已知交易所錢包的 ETH，以及 100 萬美元以上的巨鯨轉帳。");
  return uiText("包含以太坊上所有進出已知交易所錢包的 {{p0}}。", { p0: asset });
}

/** Net flow as words, never as a buy or sell signal: which way the coins went. */
function NetFlow({ totals, asset, locale }: { totals: FlowTotals; asset: string; locale: UiLocale }) {
  if (!totals.inflow && !totals.outflow)
    return <p className="sm-net sm-net-none">{uiText("沒有記錄到交易所進出")}</p>;
  const into = totals.net >= 0;
  return (
    <p className={into ? "sm-net sm-net-in" : "sm-net sm-net-out"}>
      <span>{into ? uiText("淨流入交易所") : uiText("淨流出交易所")}</span>
      <strong>
        {formatAmount(Math.abs(totals.net), locale)} {asset}
      </strong>
      <small>{formatUsd(Math.abs(totals.net_usd), locale)}</small>
    </p>
  );
}

function FlowBars({ series, locale, label, legend = true }: {
  series: FlowSeries;
  locale: UiLocale;
  label: string;
  /** The overview cards are narrow; the detail chart below carries the legend. */
  legend?: boolean;
}) {
  const scale = seriesScale(series.points);
  const n = series.points.length;
  const when = (t: number) =>
    new Date(t).toLocaleString(locale, series.step === "hour"
      ? { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" }
      : { month: "numeric", day: "numeric" });
  if (!n) return null;
  return (
    <figure className="sm-bars">
      <svg viewBox={`0 0 ${n} 100`} preserveAspectRatio="none" role="img" aria-label={label}>
        <line x1="0" x2={n} y1="50" y2="50" className="sm-axis" />
        {series.points.map((p, i) => (
          <g key={p.t}>
            <title>
              {`${when(p.t)}  ${uiText("流入")} ${formatUsd(p.inflow_usd, locale)} · ${uiText("流出")} ${formatUsd(p.outflow_usd, locale)}`}
            </title>
            {p.inflow_usd > 0 && (
              <rect className="sm-bar-in" x={i + 0.15} width={0.7}
                y={50 - (p.inflow_usd / scale) * 48} height={(p.inflow_usd / scale) * 48} />
            )}
            {p.outflow_usd > 0 && (
              <rect className="sm-bar-out" x={i + 0.15} width={0.7}
                y={50} height={(p.outflow_usd / scale) * 48} />
            )}
            {/* An invisible full-height target so a hover anywhere in the column shows its numbers. */}
            <rect x={i} width={1} y={0} height={100} fill="transparent" />
          </g>
        ))}
      </svg>
      <figcaption>
        <span>{when(series.points[0].t)}</span>
        {legend && (
          <span className="sm-legend">
            <i className="sm-bar-in" /> {uiText("流入交易所")}
            <i className="sm-bar-out" /> {uiText("流出交易所")}
          </span>
        )}
        <span>{when(series.points[n - 1].t)}</span>
      </figcaption>
    </figure>
  );
}

function eventTitle(event: FlowEvent): string {
  if (event.kind === "exchange_in") return uiText("流入 {{p0}}", { p0: event.to_entity ?? "" });
  if (event.kind === "exchange_out") return uiText("流出 {{p0}}", { p0: event.from_entity ?? "" });
  if (event.kind === "exchange_move")
    return uiText("{{p0}} 轉到 {{p1}}", { p0: event.from_entity ?? "", p1: event.to_entity ?? "" });
  return uiText("巨鯨轉帳");
}

function EventList({ events, asset, locale }: { events: FlowEvent[]; asset: string; locale: UiLocale }) {
  if (!events.length)
    return <p className="sm-empty">{uiText("這段期間沒有 100 萬美元以上的單筆移動。")}</p>;
  return (
    <ol className="sm-events">
      {events.map((event) => (
        <li key={`${event.tx}:${event.kind}:${event.to_address}`} className={`sm-event sm-event-${event.kind}`}>
          <div className="sm-event-main">
            <strong>{eventTitle(event)}</strong>
            <span className="sm-event-route">
              {event.from_entity ?? shortAddress(event.from_address)} → {event.to_entity ?? shortAddress(event.to_address)}
            </span>
          </div>
          <div className="sm-event-value">
            <strong>{formatAmount(event.amount, locale)} {asset}</strong>
            <span>{formatUsd(event.usd, locale)}</span>
          </div>
          <a className="sm-event-time" href={explorerUrl(event)} target="_blank" rel="noreferrer"
            title={uiText("在區塊鏈瀏覽器查看")}>
            {new Date(event.ts).toLocaleString(locale, { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" })}
            <Icon name="arrow" />
          </a>
        </li>
      ))}
    </ol>
  );
}

function Freshness({ sync, stale, locale }: { sync: SmartMoneyOverview["sync"]; stale?: boolean; locale: UiLocale }) {
  const last = lastSynced(sync);
  if (stale) return <p className="sm-fresh sm-fresh-late" role="status">{uiText("暫時無法連線到 txinTrade 雲端，顯示的是上次取得的資料。")}</p>;
  if (!last) return <p className="sm-fresh">{uiText("鏈上監看剛啟動，資料會在幾分鐘內出現。")}</p>;
  const time = new Date(last).toLocaleTimeString(locale, { hour: "2-digit", minute: "2-digit" });
  const late = Date.now() - last > SYNC_LATE_MS;
  return (
    <p className={late ? "sm-fresh sm-fresh-late" : "sm-fresh"}>
      {late ? uiText("鏈上資料延遲，最後更新於 {{p0}}。", { p0: time }) : uiText("每 5 分鐘更新，最後更新於 {{p0}}。", { p0: time })}
    </p>
  );
}

export default function SmartMoneyPanel({ marketId, locale }: { marketId: string; locale: UiLocale }) {
  const [overview, setOverview] = useState<SmartMoneyOverview | null>(null);
  const [tracked, setTracked] = useState<TrackedAsset[] | null>(null);
  const [detail, setDetail] = useState<SmartMoneyAsset | null>(null);
  const [error, setError] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const [loading, setLoading] = useState(false);
  const marketAsset = assetForMarket(marketId);
  const [asset, setAsset] = useState<string | null>(null);
  const [span, setSpan] = useState<FlowWindow>("1d");

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    Promise.all([
      load<SmartMoneyOverview>("/overview", controller.signal),
      load<{ assets: TrackedAsset[] }>("/assets", controller.signal),
    ])
      .then(([data, list]) => {
        setOverview(data);
        setTracked(list.assets);
        setError(false);
      })
      .catch(() => !controller.signal.aborted && setError(true))
      .finally(() => !controller.signal.aborted && setLoading(false));
    const timer = setInterval(() => setRefresh((n) => n + 1), REFRESH_MS);
    return () => {
      controller.abort();
      clearInterval(timer);
    };
  }, [refresh]);

  const isTracked = (symbol: string) => tracked?.some((t) => t.asset === symbol) ?? false;
  // Follow the market chosen elsewhere in the app, until a coin is picked here.
  const shown = asset ?? (isTracked(marketAsset) ? marketAsset : "ETH");

  useEffect(() => {
    if (!tracked) return;
    const controller = new AbortController();
    load<SmartMoneyAsset>(`/assets/${shown}?window=${span}`, controller.signal)
      .then(setDetail)
      .catch(() => !controller.signal.aborted && setDetail(null));
    return () => controller.abort();
  }, [shown, span, tracked, refresh]);

  const stable = overview?.stablecoins;
  const options = useMemo(() => tracked?.map((t) => t.asset) ?? [], [tracked]);

  return (
    <div className="smart-money">
      <div className="page-title">
        <div>
          <h1>{uiText("聰明錢")}</h1>
          <p>{uiText("追蹤大額資金進出交易所與巨鯨轉帳。資金流入交易所常是準備賣出，流出多是提領持有，但只是線索，不是買賣訊號。")}</p>
        </div>
        <button className="secondary-button" disabled={loading} onClick={() => setRefresh((n) => n + 1)}>
          <Icon name="refresh" />
          {loading ? uiText("讀取中…") : uiText("重新整理")}
        </button>
      </div>
      {error && !overview && (
        <div className="alert" role="alert">{uiText("無法讀取鏈上資料，請檢查網路後重新整理。")}</div>
      )}
      {overview && (
        <>
          <Freshness sync={overview.sync} stale={overview.stale} locale={locale} />
          <section className="sm-overview" aria-label={uiText("BTC 與 ETH 總覽")}>
            {overview.assets.map((a) => (
              <article key={a.asset} className="panel sm-card">
                <header>
                  <h2>{a.asset}</h2>
                  <span>{uiText("過去 24 小時")}</span>
                </header>
                <NetFlow totals={a["1d"]} asset={a.asset} locale={locale} />
                <dl className="sm-figures">
                  <div><dt>{uiText("流入")}</dt><dd>{formatUsd(a["1d"].inflow_usd, locale)}</dd></div>
                  <div><dt>{uiText("流出")}</dt><dd>{formatUsd(a["1d"].outflow_usd, locale)}</dd></div>
                  <div><dt>{uiText("巨鯨轉帳")}</dt><dd>{a["1d"].whale_count}</dd></div>
                  <div><dt>{uiText("7 天交易所淨流入")}</dt><dd>{formatUsd(a["7d"].net_usd, locale, true)}</dd></div>
                </dl>
                <FlowBars series={a.series} locale={locale} legend={false} label={uiText("{{p0}} 過去 7 天每小時交易所流入與流出", { p0: a.asset })} />
              </article>
            ))}
            {stable && (
              <article className="panel sm-card sm-stable">
                <header>
                  <h2>{uiText("穩定幣")}</h2>
                  <span>USDT · USDC</span>
                </header>
                <p className="sm-net">
                  <span>{uiText("總供給")}</span>
                  <strong>{stable.total_usd ? formatUsd(stable.total_usd, locale) : "—"}</strong>
                </p>
                <dl className="sm-figures">
                  <div><dt>{uiText("24 小時變化")}</dt><dd>{stable.change_1d == null ? "—" : formatUsd(stable.change_1d, locale, true)}</dd></div>
                  <div><dt>{uiText("7 天變化")}</dt><dd>{stable.change_7d == null ? "—" : formatUsd(stable.change_7d, locale, true)}</dd></div>
                  <div><dt>{uiText("24 小時交易所淨流入")}</dt><dd>{formatUsd(stable.exchange["1d"].net_usd, locale, true)}</dd></div>
                  <div><dt>{uiText("7 天交易所淨流入")}</dt><dd>{formatUsd(stable.exchange["7d"].net_usd, locale, true)}</dd></div>
                </dl>
                <p className="sm-note">{uiText("供給增加代表新資金進場；穩定幣流入交易所，通常是準備買進的資金。")}</p>
              </article>
            )}
          </section>

          <section className="panel sm-detail" aria-labelledby="sm-detail-title">
            <div className="sm-detail-head">
              <div>
                <h2 id="sm-detail-title">{uiText("{{p0}} 資金流向", { p0: shown })}</h2>
                {detail && <p className="sm-note">{coverageNote(detail.coverage, shown)}</p>}
              </div>
              <div className="sm-controls">
                <label className="sm-select">
                  <span className="control-label">{uiText("幣種")}</span>
                  <select value={shown} onChange={(e) => setAsset(e.target.value)}>
                    {options.map((o) => <option key={o} value={o}>{o}</option>)}
                  </select>
                </label>
                <div className="segments" role="group" aria-label={uiText("期間")}>
                  {FLOW_WINDOWS.map((w) => (
                    <button key={w} type="button" aria-pressed={span === w}
                      className={span === w ? "chosen" : ""} onClick={() => setSpan(w)}>
                      {windowLabel(w)}
                    </button>
                  ))}
                </div>
              </div>
            </div>
            {tracked && !isTracked(marketAsset) && asset === null && (
              <p className="sm-untracked">
                {uiText("{{p0}} 不在以太坊上或尚未追蹤，沒有鏈上資料；先顯示 ETH。", { p0: marketAsset })}
              </p>
            )}
            {detail && (
              <>
                <NetFlow totals={detail.totals} asset={shown} locale={locale} />
                <dl className="sm-figures sm-figures-wide">
                  <div><dt>{uiText("流入")}</dt><dd>{formatAmount(detail.totals.inflow, locale)} <small>{formatUsd(detail.totals.inflow_usd, locale)}</small></dd></div>
                  <div><dt>{uiText("流出")}</dt><dd>{formatAmount(detail.totals.outflow, locale)} <small>{formatUsd(detail.totals.outflow_usd, locale)}</small></dd></div>
                  <div><dt>{uiText("巨鯨轉帳")}</dt><dd>{detail.totals.whale_count}</dd></div>
                </dl>
                <FlowBars series={detail.series} locale={locale}
                  label={uiText("{{p0}} 交易所流入與流出", { p0: shown })} />
                <h3 className="sm-events-title">{uiText("100 萬美元以上的單筆移動")}</h3>
                <EventList events={detail.events} asset={shown} locale={locale} />
              </>
            )}
          </section>
          <p className="sm-sources">
            {uiText("資料來源：mempool.space、以太坊公開節點、Blockscout 公開錢包標籤、DefiLlama；由 txinTrade 雲端每 5 分鐘整理，保留 90 天。")}
          </p>
        </>
      )}
    </div>
  );
}
