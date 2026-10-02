import { uiText } from "./i18n/index.ts";

// Shared wording for the computer's cloud sign-in and remote-access state.

export type RemoteStatus = { enabled: boolean; state: string; error: string | null; device_id: string | null };
type Tone = "ok" | "pending" | "warn" | "off";

export function remoteView(remote: RemoteStatus): { tone: Tone; text: string } {
  if (!remote.enabled) return { tone: "off", text: uiText("已關閉：其他裝置無法存取這台電腦。") };
  switch (remote.state) {
    case "connected": return { tone: "ok", text: uiText("已連線：可從其他裝置的瀏覽器使用這台電腦。") };
    case "registering": return { tone: "pending", text: uiText("正在把這台電腦註冊到雲端帳戶…") };
    case "reconnecting": return { tone: "pending", text: uiText("連線中斷，正在自動重新連線…") };
    case "device_already_connected": return { tone: "pending", text: uiText("這台電腦已有另一個連線，稍後會自動重試。") };
    case "subscription_required": return { tone: "warn", text: uiText("遠端存取需要有效的訂閱方案。") };
    case "device_limit": return { tone: "warn", text: uiText("此帳戶可註冊的電腦已達上限；請先在其他電腦關閉遠端存取。") };
    case "signed_out": return { tone: "warn", text: uiText("雲端登入已失效，請重新登入。") };
    case "error": return { tone: "warn", text: cloudErrorText(remote.error ?? "") };
    default: return { tone: "pending", text: uiText("正在連線到雲端…") };
  }
}

export function cloudErrorText(code: string): string {
  switch (code) {
    case "timeout": return uiText("登入逾時，請重新登入。");
    case "cancelled": return uiText("登入已取消或未完成，請重新登入。");
    case "rejected": return uiText("雲端服務未接受這次登入，請重新登入。");
    case "invalid_response": return uiText("雲端服務回應不完整，請重新登入。");
    case "storage_failed": return uiText("無法在這台電腦保存登入資訊；請確認資料目錄可寫入後重試。");
    case "misconfigured": return uiText("雲端服務位址設定不正確。");
    default: return uiText("無法連線到雲端服務，請稍後重試。");
  }
}
