import { useEffect, useRef, useState } from "react";
import { uiText, uiLocale } from "./i18n/index.ts";
import { AnalysisSpinner } from "./AnalysisProgress";
import Icon from "./Icon";
import { settingsRequest, type IntegrationStatus } from "./localSettings";
import { binanceFailureMessage, type ExchangeSyncSummary } from "./binanceIntegration";
import "./ExchangeSyncPanel.css";

type Provider = "bingx" | "binance";
type Status = IntegrationStatus & { contracts: string[]; last_sync?: ExchangeSyncSummary | null };
// Adding an exchange is one entry here: the panel stays one row of chips however many there are.
const providers: Provider[] = ["bingx", "binance"];
const titles: Record<Provider, string> = { bingx: "BingX", binance: "Binance" };
const contracts = (provider: Provider) => provider === "binance" ? uiText("USDT 永續") : uiText("永續與標準合約");

function date(value: string): string {
  const parsed = new Date(value);
  return Number.isFinite(parsed.getTime())
    ? parsed.toLocaleString(uiLocale(), { hour12: false, month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" })
    : uiText("未提供");
}

const counts = (summary: ExchangeSyncSummary) => uiText("新增 {{p0}} · 更新 {{p1}} · 關閉 {{p2}} · 略過 {{p3}}",
  { p0: summary.created, p1: summary.updated, p2: summary.closed, p3: summary.unsupported });

export default function ExchangeSyncPanel({ onSynced, onOpenSettings }: {
  onSynced: () => Promise<void>; onOpenSettings: () => void;
}) {
  const [statuses, setStatuses] = useState<Partial<Record<Provider, Status>>>({});
  const [errors, setErrors] = useState<Partial<Record<Provider, string>>>({});
  const [busyProvider, setBusyProvider] = useState<Provider | null>(null);
  /** The exchanges that synced in the last run, whose combined result the footnote reports. */
  const [justSynced, setJustSynced] = useState<Provider[]>([]);
  const pending = useRef(false);
  const mounted = useRef(true);
  useEffect(() => {
    let active = true;
    mounted.current = true;
    for (const provider of providers) {
      settingsRequest<Status>(`/integrations/${provider}`)
        .then(status => { if (active) setStatuses(current => ({ ...current, [provider]: status })); })
        .catch((error: Error) => { if (active) setErrors(current => ({ ...current, [provider]: provider === "binance" ? binanceFailureMessage(error) : error.message })); });
    }
    return () => { active = false; mounted.current = false; };
  }, []);

  /** Sync one exchange; returns whether the position list should be reloaded. */
  async function syncOne(provider: Provider): Promise<boolean> {
    setBusyProvider(provider);
    setErrors(current => ({ ...current, [provider]: "" }));
    try {
      const result = await settingsRequest<ExchangeSyncSummary>(`/positions/${provider}/sync`, { method: "POST" });
      if (!mounted.current) return false;
      setStatuses(current => ({ ...current, [provider]: { ...current[provider]!, last_sync: result } }));
      return true;
    } catch (error) {
      if (mounted.current) setErrors(current => ({ ...current, [provider]: provider === "binance" ? binanceFailureMessage(error) : (error as Error).message }));
      return false;
    }
  }

  async function sync(targets: Provider[]) {
    if (pending.current || !targets.length) return;
    pending.current = true;
    const synced: Provider[] = [];
    try {
      // One at a time: each exchange keeps its own result, and one failure never hides another's success.
      for (const provider of targets) if (await syncOne(provider)) synced.push(provider);
      const changed = synced.length > 0;
      if (mounted.current) setJustSynced(synced);
      if (changed && mounted.current) {
        try {
          await onSynced();
        } catch (error) {
          const last = targets[targets.length - 1];
          if (mounted.current) setErrors(current => ({ ...current, [last]: uiText("同步已完成，但持倉清單更新失敗：{{p0}}", { p0: (error as Error).message }) }));
        }
      }
    } finally {
      pending.current = false;
      if (mounted.current) setBusyProvider(null);
    }
  }

  const connected = providers.filter(provider => statuses[provider]?.configured);
  const needsKeys = providers.filter(provider => {
    const status = statuses[provider];
    return status && !status.configured && (status.needs_reentry || status.needs_migration);
  });
  const unconnected = providers.filter(provider => statuses[provider] && !statuses[provider]!.configured && !needsKeys.includes(provider));
  const loading = providers.some(provider => !statuses[provider] && !errors[provider]);
  const latest = connected.map(provider => statuses[provider]?.last_sync?.synced_at).filter((value): value is string => !!value).sort().at(-1);
  const failures = providers.filter(provider => errors[provider]);
  const results = justSynced.map(provider => statuses[provider]?.last_sync).filter((value): value is ExchangeSyncSummary => !!value);
  const report = results.length ? results.reduce((total, item) => ({ ...total,
    created: total.created + item.created, updated: total.updated + item.updated,
    closed: total.closed + item.closed, unsupported: total.unsupported + item.unsupported })) : null;

  return <section className="exchange-sync" aria-labelledby="exchange-sync-title">
    <div className="exchange-sync-head">
      <h2 id="exchange-sync-title">{uiText("交易所持倉")}</h2>
      <div className="exchange-sync-chips">
        {connected.map(provider => {
          const summary = statuses[provider]?.last_sync;
          const running = busyProvider === provider;
          const detail = [contracts(provider), summary ? uiText("最後成功同步：{{p0}}", { p0: date(summary.synced_at) }) : uiText("尚未成功同步"),
            summary ? counts(summary) : ""].filter(Boolean).join("\n");
          return <button type="button" className="exchange-chip" key={provider} data-state={errors[provider] ? "error" : summary ? "synced" : "idle"}
            disabled={!!busyProvider} aria-busy={running} title={detail}
            aria-label={`${uiText("同步 {{p0}} 持倉", { p0: titles[provider] })} · ${detail.replace(/\n/g, " · ")}`}
            onClick={() => void sync([provider])}>
            {running ? <AnalysisSpinner /> : <i aria-hidden="true" />}
            {titles[provider]}
            {summary && <b>{summary.active}</b>}
          </button>;
        })}
        {needsKeys.map(provider => (
          <button type="button" className="exchange-chip" data-state="error" key={provider} onClick={onOpenSettings}>
            <i aria-hidden="true" />{titles[provider]} · {uiText("請重新輸入金鑰")}
          </button>
        ))}
        {!!unconnected.length && (
          <button type="button" className="exchange-chip exchange-chip-add" onClick={onOpenSettings}
            title={unconnected.map(provider => titles[provider]).join(" · ")}>
            <Icon name="plus" />{uiText("連接交易所")}
          </button>
        )}
        {loading && <span className="exchange-sync-loading"><AnalysisSpinner /></span>}
      </div>
      {connected.length > 0 && (
        <button type="button" className="exchange-sync-all" disabled={!!busyProvider} aria-busy={!!busyProvider}
          onClick={() => void sync(connected)}>
          {busyProvider ? <AnalysisSpinner /> : <Icon name="refresh" />}
          {busyProvider ? uiText("同步中…") : uiText("全部同步")}
        </button>
      )}
    </div>
    {failures.map(provider => (
      <p className="exchange-sync-error" role="alert" key={provider}>{titles[provider]}：{errors[provider]}</p>
    ))}
    <p className="exchange-sync-note">
      {report ? uiText("{{p0}} 已同步：{{p1}}", { p0: new Intl.ListFormat(uiLocale(), { type: "conjunction" }).format(justSynced.map(provider => titles[provider])), p1: counts(report) })
        : latest ? uiText("上次同步 {{p0}}", { p0: date(latest) }) : connected.length ? uiText("尚未成功同步") : null}
      {(report || latest || connected.length > 0) && " · "}
      {uiText("同步只讀取持倉，不會下單或自動分析。")}
    </p>
  </section>;
}
