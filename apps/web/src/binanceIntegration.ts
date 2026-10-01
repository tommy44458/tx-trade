import { uiText } from "./i18n/index.ts";
import { SettingsRequestError } from "./localSettings.ts";

export type ExchangeSyncSummary = {
  created: number; updated: number; closed: number; active: number; active_symbols: string[];
  unsupported: number; synced_at: string;
};
export type BinanceReadTest = {
  readable: true; active: number; active_symbols: string[]; unsupported: number;
  started_at: string; completed_at: string;
  permissions: {
    verified: boolean; read_only: boolean | null; reading: boolean | null;
    write_permissions: string[]; error_code?: string;
  };
};

export function hasVerifiedReadOnlyPermissions(test: BinanceReadTest): boolean {
  const permissions = test.permissions;
  return permissions.verified === true && permissions.read_only === true && permissions.reading === true
    && Array.isArray(permissions.write_permissions) && permissions.write_permissions.length === 0;
}

export function binanceWritePermissionLabel(permission: string): string {
  const labels: Record<string, string> = {
    enableWithdrawals: uiText("提幣"),
    enableInternalTransfer: uiText("內部轉帳"),
    enableMargin: uiText("槓桿交易"),
    enableFutures: uiText("合約交易"),
    permitsUniversalTransfer: uiText("萬向轉帳"),
    enableVanillaOptions: uiText("期權交易"),
    enableFixApiTrade: uiText("FIX API 交易"),
    enableSpotAndMarginTrading: uiText("現貨與槓桿交易"),
    enablePortfolioMarginTrading: uiText("統一帳戶交易"),
  };
  return Object.hasOwn(labels, permission) ? labels[permission] : permission;
}

export function binanceFailureMessage(error: unknown): string {
  if (!(error instanceof SettingsRequestError)) return error instanceof Error ? error.message : uiText("讀取失敗，請稍後再試。");
  const messages: Record<string, string> = {
    NOT_CONFIGURED: uiText("請先在設定儲存 Binance API Key 與 Secret。"),
    INVALID_CREDENTIALS: uiText("Binance 金鑰資料不完整，請重新儲存。"),
    AUTHENTICATION_FAILED: uiText("Binance 拒絕讀取帳戶。請核對金鑰、IP 限制、讀取權限與合約帳戶狀態；不需開啟交易權限。"),
    TIMEOUT: uiText("Binance 回應逾時，本次未更新持倉。"),
    NETWORK_ERROR: uiText("無法連線 Binance，請檢查網路後再試。"),
    RATE_LIMITED: uiText("Binance 已限制請求，請等候後再試。"),
    IP_BANNED: uiText("Binance 暫時限制此 IP，請等限制解除後再試。"),
    TIMESTAMP_ERROR: uiText("校時後仍無法通過 Binance 時間檢查，請稍後再試。"),
    SIGNATURE_ERROR: uiText("Binance 無法驗證簽名，請核對 API Key 與 Secret。"),
    UPSTREAM_ERROR: uiText("Binance 帳戶服務暫時無法使用，請稍後再試。"),
    INVALID_RESPONSE: uiText("Binance 回傳資料格式不完整，本次未更新持倉。"),
    INVALID_POSITION: uiText("Binance 持倉資料不完整或不一致，本次未更新持倉。"),
    INVALID_CONFIG: uiText("Binance 合約設定不完整或不一致，本次未更新持倉。"),
    MARKET_CATALOG_UNAVAILABLE: uiText("無法取得已確認的 USDT 永續交易對，本次未更新持倉。"),
    UNSUPPORTED_ENDPOINT: uiText("目前不支援此 Binance 帳戶操作。"),
    BINANCE_ACCOUNT_CHANGED: uiText("Binance 帳戶連線已變更，請重新同步。"),
    BINANCE_CREDENTIALS_UNAVAILABLE: uiText("無法讀取本機保存的 Binance 金鑰，請重新儲存。"),
    UNSUPPORTED_ACCOUNT_MODE: uiText("目前僅支援已啟用合約的普通 USDⓈ-M 帳戶，不支援 Portfolio Margin；原有持倉未更新。"),
    ACCOUNT_MODE_UNVERIFIED: uiText("無法確認幣安帳戶模式，原有持倉未更新。請稍後再測試讀取。"),
    BINANCE_SYNC_SUPERSEDED: uiText("較新的同步已完成，本次較舊資料未套用；請重新整理持倉清單。"),
  };
  const message = error.code && Object.hasOwn(messages, error.code) ? messages[error.code] : error.message;
  const codes = [error.code, error.exchangeCode].filter(value => value != null).join(" / ");
  return message + (codes ? ` (${codes})` : "")
    + (error.retryAfter != null ? " " + uiText("請至少等候 {{p0}} 秒後再試。", { p0: error.retryAfter }) : "");
}
