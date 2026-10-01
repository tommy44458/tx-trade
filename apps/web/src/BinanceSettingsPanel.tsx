import { useEffect, useRef, useState, type FormEvent } from "react";
import { uiText } from "./i18n/index.ts";
import { AnalysisSpinner } from "./AnalysisProgress";
import { settingsRequest, type IntegrationStatus, type LocalSettings } from "./localSettings";
import { binanceFailureMessage, binanceWritePermissionLabel, hasVerifiedReadOnlyPermissions, type BinanceReadTest } from "./binanceIntegration";

export default function BinanceSettingsPanel({ integration, busy, onBusyChange, onUpdated }: {
  integration?: IntegrationStatus; busy: boolean; onBusyChange: (busy: boolean) => void;
  onUpdated: (settings: LocalSettings) => Promise<void>;
}) {
  const [key, setKey] = useState("");
  const [secret, setSecret] = useState("");
  const [action, setAction] = useState<"save" | "test" | "remove" | null>(null);
  const [error, setError] = useState("");
  const [readError, setReadError] = useState("");
  const [notice, setNotice] = useState<"saved" | "removed" | null>(null);
  const [test, setTest] = useState<BinanceReadTest | null>(null);
  const pending = useRef(false);
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);

  async function perform(next: "save" | "test" | "remove", operation: () => Promise<void>) {
    if (pending.current || busy) return;
    pending.current = true;
    setAction(next);
    onBusyChange(true);
    setError("");
    setNotice(null);
    if (next === "test") { setReadError(""); setTest(null); }
    try {
      await operation();
    } catch (reason) {
      if (mounted.current) {
        if (next === "test") setReadError(binanceFailureMessage(reason));
        else setError(binanceFailureMessage(reason));
      }
    } finally {
      pending.current = false;
      if (mounted.current) { setAction(null); onBusyChange(false); }
    }
  }

  async function updateSettings(values: Record<string, string | boolean>, result: "saved" | "removed") {
    const next = await settingsRequest<LocalSettings>("/settings", { method: "PATCH", body: JSON.stringify(values) });
    if (!mounted.current) return;
    setKey(""); setSecret(""); setTest(null); setReadError(""); setNotice(result);
    try {
      await onUpdated(next);
    } catch {
      if (mounted.current) setError(uiText("Binance 金鑰設定已儲存，但畫面更新未完成。請重新開啟設定確認。"));
    }
  }

  function save(event: FormEvent) {
    event.preventDefault();
    void perform("save", async () => {
      if (!key.trim() || !secret.trim()) throw new Error(uiText("請一併輸入 Binance API Key 與 Secret"));
      await updateSettings({ binance_api_key: key.trim(), binance_api_secret: secret.trim() }, "saved");
    });
  }

  const configured = integration?.configured === true;
  const disabled = busy || action !== null;
  const hasDraft = Boolean(key || secret);
  const verifiedReadOnly = test && hasVerifiedReadOnlyPermissions(test);
  function changeCredential(field: "key" | "secret", value: string) {
    if (field === "key") setKey(value); else setSecret(value);
    setTest(null); setReadError(""); setError(""); setNotice(null);
  }

  return <section className="panel settings-section settings-binance" aria-labelledby="settings-binance-title">
    <div className="panel-head">
      <h2 id="settings-binance-title">{uiText("Binance 持倉同步")}<small className="settings-optional">{uiText("選填")}</small></h2>
      <span className={`settings-status${test ? " connected" : ""}`}>
        {readError ? uiText("讀取失敗") : test ? uiText("讀取測試成功") : configured ? uiText("已儲存，尚未測試") : uiText("未設定")}
      </span>
    </div>
    <p className="settings-help">{uiText("讀取普通合約帳戶的 USDT 永續持倉。未設定時，仍可手動新增持倉。")}</p>
    <p className="settings-permission-note" id="settings-binance-permissions">
      <strong>{uiText("金鑰權限")}</strong>
      <span>{uiText("僅開啟 Enable Reading（讀取）權限，供本應用讀取持倉。請勿開啟交易、轉帳或提幣權限。")}</span>
    </p>
    <form onSubmit={save}>
      <div className="settings-fields">
        <label className="settings-field" htmlFor="binance-api-key">API Key
          <input id="binance-api-key" type="password" value={key} disabled={disabled} maxLength={512}
            aria-describedby="settings-binance-permissions" autoComplete="off" autoCapitalize="none" spellCheck={false}
            placeholder={configured ? uiText("輸入新金鑰以更新") : uiText("輸入 API Key")}
            onChange={event => changeCredential("key", event.target.value)} />
        </label>
        <label className="settings-field" htmlFor="binance-api-secret">API Secret
          <input id="binance-api-secret" type="password" value={secret} disabled={disabled} maxLength={512}
            aria-describedby="settings-binance-permissions" autoComplete="off" autoCapitalize="none" spellCheck={false}
            placeholder={uiText("輸入 API Secret")} onChange={event => changeCredential("secret", event.target.value)} />
        </label>
      </div>
      <div className="settings-actions">
        <button type="submit" className="action" disabled={disabled || !hasDraft} aria-busy={action === "save"}>
          {action === "save" && <AnalysisSpinner />}{uiText("儲存 Binance 金鑰")}
        </button>
        <button type="button" className="settings-secondary" disabled={disabled || !configured || hasDraft} aria-busy={action === "test"}
          onClick={() => void perform("test", async () => {
            const result = await settingsRequest<BinanceReadTest>("/integrations/binance/test", { method: "POST" });
            if (result?.readable !== true || !result.permissions || !Array.isArray(result.permissions.write_permissions)
                || !Number.isInteger(result.active) || result.active < 0) throw new Error(uiText("讀取測試回傳資料不完整，請稍後再試。"));
            if (mounted.current) setTest(result);
          })}>
          {action === "test" && <AnalysisSpinner />}{uiText("測試持倉讀取")}
        </button>
        {(configured || integration?.needs_reentry || integration?.needs_migration) && <button type="button" className="settings-secondary" disabled={disabled}
          onClick={() => void perform("remove", () => updateSettings({ clear_binance: true }, "removed"))}>{uiText("移除金鑰")}</button>}
      </div>
    </form>
    <p className="settings-help">{hasDraft ? uiText("請先儲存新金鑰，再測試持倉讀取。") : uiText("測試不會新增或修改本地持倉；同步請到「我的持倉」。")}</p>
    {notice && <p className="settings-inline-success" role="status">{notice === "saved" ? uiText("Binance 金鑰已儲存，讀取權限尚未驗證。") : uiText("Binance 金鑰已移除，既有持倉紀錄已保留。")}</p>}
    {error && <p className="settings-inline-error" role="alert">{error}</p>}
    {readError && <p className="settings-inline-error" role="alert">{readError}</p>}
    {test && <div className="settings-read-test" role="status">
      <p>{uiText("持倉讀取成功：{{p0}} 筆，略過 {{p1}} 筆。", { p0: test.active, p1: test.unsupported })}</p>
      <p className={verifiedReadOnly ? "settings-inline-success" : "settings-permission-warning"}>
        {verifiedReadOnly ? uiText("唯讀權限已確認。")
          : test.permissions.write_permissions.length ? uiText("持倉可讀，但有多餘權限。請關閉：{{p0}}。", { p0: test.permissions.write_permissions.map(binanceWritePermissionLabel).join("、") })
            : uiText("持倉可讀，但權限尚未驗證。請在 Binance 確認僅開啟 Enable Reading。")}
      </p>
    </div>}
  </section>;
}
