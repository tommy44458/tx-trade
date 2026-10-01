import { useEffect, useRef, useState } from "react";
import { uiText, uiLocale } from "./i18n/index.ts";
import { AnalysisSpinner } from "./AnalysisProgress";
import { settingsRequest, type IntegrationStatus } from "./localSettings";
import { binanceFailureMessage, type ExchangeSyncSummary } from "./binanceIntegration";
import "./ExchangeSyncPanel.css";

type Provider = "bingx" | "binance";
type Status = IntegrationStatus & { contracts: string[]; last_sync?: ExchangeSyncSummary | null };
const providers: Provider[] = ["bingx", "binance"];
const titles: Record<Provider, string> = { bingx: "BingX", binance: "Binance" };

function date(value: string): string {
  const parsed = new Date(value);
  return Number.isFinite(parsed.getTime()) ? parsed.toLocaleString(uiLocale(), { hour12: false }) : uiText("未提供");
}

export default function ExchangeSyncPanel({ onSynced, onOpenSettings }: {
  onSynced: () => Promise<void>; onOpenSettings: () => void;
}) {
  const [statuses, setStatuses] = useState<Partial<Record<Provider, Status>>>({});
  const [errors, setErrors] = useState<Partial<Record<Provider, string>>>({});
  const [busyProvider, setBusyProvider] = useState<Provider | null>(null);
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

  async function sync(provider: Provider) {
    if (pending.current || !statuses[provider]?.configured) return;
    pending.current = true;
    setBusyProvider(provider);
    setErrors(current => ({ ...current, [provider]: "" }));
    try {
      const result = await settingsRequest<ExchangeSyncSummary>(`/positions/${provider}/sync`, { method: "POST" });
      if (!mounted.current) return;
      setStatuses(current => ({ ...current, [provider]: { ...current[provider]!, last_sync: result } }));
      try {
        await onSynced();
      } catch (error) {
        if (mounted.current) setErrors(current => ({ ...current, [provider]: uiText("同步已完成，但持倉清單更新失敗：{{p0}}", { p0: (error as Error).message }) }));
      }
    } catch (error) {
      if (mounted.current) setErrors(current => ({ ...current, [provider]: provider === "binance" ? binanceFailureMessage(error) : (error as Error).message }));
    } finally {
      pending.current = false;
      if (mounted.current) setBusyProvider(null);
    }
  }

  return <section className="exchange-sync" aria-labelledby="exchange-sync-title">
    <h2 id="exchange-sync-title">{uiText("交易所持倉同步")}</h2>
    <div className="exchange-sync-rows">
      {providers.map(provider => {
        const status = statuses[provider];
        const summary = status?.last_sync;
        const running = busyProvider === provider;
        return <div className="exchange-sync-row" key={provider} data-exchange={provider}>
          <div className="exchange-sync-info">
            <div className="exchange-sync-label"><strong>{titles[provider]}</strong><span>
              {status?.configured ? provider === "binance" ? uiText("USDT 永續") : uiText("永續與標準合約")
                : status?.needs_reentry || status?.needs_migration ? uiText("請重新輸入金鑰")
                  : status ? uiText("尚未設定金鑰") : errors[provider] ? uiText("狀態讀取失敗") : uiText("讀取連線狀態…")}
            </span></div>
            {summary ? <div className="exchange-sync-summary">
              <span>{uiText("最後成功同步：{{p0}}", { p0: date(summary.synced_at) })}</span>
              <span>{uiText("新增 {{p0}} · 更新 {{p1}} · 關閉 {{p2}} · 略過 {{p3}}", { p0: summary.created, p1: summary.updated, p2: summary.closed, p3: summary.unsupported })}</span>
            </div> : <span className="exchange-sync-empty">{uiText("尚未成功同步")}</span>}
          </div>
          <button className="exchange-sync-action" type="button" disabled={!!busyProvider || (!status && !errors[provider])}
            aria-busy={running} aria-label={status?.configured ? uiText("同步 {{p0}} 持倉", { p0: titles[provider] }) : uiText("設定 {{p0}} 金鑰", { p0: titles[provider] })}
            onClick={() => status?.configured ? void sync(provider) : onOpenSettings()}>
            {running && <AnalysisSpinner />}
            {running ? uiText("同步中…") : status?.configured ? uiText("同步持倉") : uiText("前往設定")}
          </button>
          {errors[provider] && <p className="exchange-sync-error" role="alert">{errors[provider]}</p>}
        </div>;
      })}
    </div>
    <p className="exchange-sync-note">{uiText("同步只讀取持倉，不會下單或自動分析。")}</p>
  </section>;
}
