import { uiText } from "./i18n/index.ts";
import { useEffect, useState } from "react";
import { settingsRequest, type IntegrationStatus } from "./localSettings";
import { AnalysisSpinner } from "./AnalysisProgress";
import "./BingXSyncPanel.css";
import { apiFetch } from "./transport.ts";

type Status = IntegrationStatus & { contracts: string[] };
type Result = {
  created: number;
  updated: number;
  closed: number;
  active: number;
  active_symbols: string[];
  unsupported: number;
  synced_at: string;
};

export default function BingXSyncPanel({
  onSynced,
  onOpenSettings,
}: {
  onSynced: () => Promise<void>;
  onOpenSettings: () => void;
}) {
  const [status, setStatus] = useState<Status | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  useEffect(() => {
    settingsRequest<Status>("/integrations/bingx")
      .then(setStatus)
      .catch(() => setMessage(uiText("無法讀取 BingX 連線狀態")));
  }, []);
  async function sync() {
    setBusy(true);
    setMessage("");
    try {
      const response = await apiFetch("/api/v1/positions/bingx/sync", {
        method: "POST",
      });
      const body = await response.json();
      if (!response.ok)
        throw new Error(typeof body?.detail === "string" ? body.detail : body?.detail?.message || uiText("同步失敗 ({{p0}})", { p0: response.status }));
      const result = body as Result;
      await onSynced();
      setStatus((previous) => previous ? { ...previous, needs_verification: false } : previous);
      setMessage(
        uiText("同步完成：目前 {{p0}} 筆{{p1}}，新增 {{p2}} 筆，更新 {{p3}} 筆，結束 {{p4}} 筆{{p5}}。", { p0: result.active, p1: result.active_symbols.length ? `（${result.active_symbols.join("、")}）` : "", p2: result.created, p3: result.updated, p4: result.closed, p5: result.unsupported ? uiText("；{{p0}} 筆非支援交易對未匯入", { p0: result.unsupported }) : "" }),
      );
    } catch (error) {
      setMessage((error as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="bingx-sync" aria-label={uiText("BingX 持倉同步")}>
      <div className="bingx-sync-row">
        <div className="bingx-sync-info">
          <h2>{uiText("BingX 持倉同步")}</h2>
          <span>{status?.configured ? uiText("永續與標準合約") : status?.needs_reentry || status?.needs_migration ? uiText("請重新輸入金鑰") : status ? uiText("尚未設定金鑰") : uiText("讀取連線狀態…")}</span>
        </div>
        <button
          className="bingx-sync-action"
          type="button"
          disabled={busy || !status}
          aria-busy={busy}
          onClick={status?.configured ? sync : onOpenSettings}
        >
          {busy && <AnalysisSpinner />}{" "}
          {busy ? uiText("同步中…") : status?.configured ? uiText("同步持倉") : uiText("前往設定")}
        </button>
      </div>
      {(status?.needs_reentry || status?.needs_migration) && <p className="bingx-sync-note">{uiText("請在設定重新儲存 BingX 金鑰，之後同步不再要求系統密碼。")}</p>}{" "}
      {status?.needs_verification && (
        <p className="bingx-sync-note">{uiText("同步時會確認已儲存的金鑰。")}</p>
      )}
      <details className="compact-details">
        <summary>{uiText("同步範圍與設定")}</summary>
        <p className="panel-copy">{uiText("支援幣安所有可交易的 USDT 永續交易對。金鑰加密保存在本機 SQLite；同步僅讀取持倉，不會下單。交易所未提供的止損與止盈可手動補填。")}</p>
      </details>
      {message && (
        <p className="bingx-sync-note" role="status">
          {message}
        </p>
      )}
    </section>
  );
}
