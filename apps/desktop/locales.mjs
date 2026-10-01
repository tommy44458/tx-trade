import { existsSync } from "node:fs";
import { join } from "node:path";
import { DatabaseSync } from "node:sqlite";

export const NATIVE_STRINGS = Object.freeze({
  "zh-TW": {
    settings: "設定", openData: "開啟資料目錄", openLog: "開啟後端記錄",
    edit: "編輯", view: "顯示方式", window: "視窗",
    starting: "正在啟動", failed: "本地後端未能啟動",
    preparing: "正在準備本地服務與資料庫。",
    failureBody: "無法準備本地資料庫或啟動服務。請檢查資料目錄是否可寫入，以及是否有足夠的磁碟空間。",
    failureHint: "選單「設定 → 開啟資料目錄」可查看資料；「開啟後端記錄」可查看錯誤原因。",
    retry: "重新啟動", retryFailed: "無法重新啟動，請查看後端記錄。",
  },
  "en-US": {
    settings: "Settings", openData: "Open Data Folder", openLog: "Open Backend Log",
    edit: "Edit", view: "View", window: "Window",
    starting: "Starting up", failed: "The local service could not start",
    preparing: "Preparing the local service and database.",
    failureBody: "The local database or service could not start. Check that the data folder is writable and that enough disk space is available.",
    failureHint: "Use Settings → Open Data Folder to view your data, or Open Backend Log to check the error.",
    retry: "Restart", retryFailed: "Restart failed. Check the backend log for details.",
  },
});

export function validateLocale(locale) {
  if (locale !== "zh-TW" && locale !== "en-US") throw new Error("Unsupported language");
  return locale;
}

// Read only the non-secret language preference. This never creates a database,
// migrates its schema or opens the credential store during startup.
export function readSavedLocale(dataDirectory, userId = "local-demo") {
  const path = join(dataDirectory, "trade_helper.sqlite3");
  if (!existsSync(path)) return "zh-TW";
  let db;
  try {
    db = new DatabaseSync(path, { readOnly: true });
    const row = db.prepare("SELECT json_extract(value_json,'$.ui_locale') AS locale "
      + "FROM app_preferences WHERE user_id=?").get(userId);
    return row?.locale === "en-US" ? "en-US" : "zh-TW";
  } catch { return "zh-TW"; }
  finally { db?.close(); }
}
