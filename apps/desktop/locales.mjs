import { existsSync } from "node:fs";
import { join } from "node:path";
import { DatabaseSync } from "node:sqlite";

export const NATIVE_STRINGS = Object.freeze({
  "zh-TW": {
    settings: "設定", openData: "開啟資料目錄", openLog: "開啟後端記錄",
    about: "關於 txinTrade", changelog: "版本變更紀錄", close: "關閉",
    releaseChannel: "更新渠道", stableChannel: "正式版", betaChannel: "測試版",
    unreleasedBuild: "此為開發版本。", noReleaseNotes: "此版本未附變更紀錄。",
    checkUpdates: "檢查更新", updateStatus: "更新狀態", installUpdate: "安裝更新",
    downloadUpdate: "下載更新", restartNow: "重新啟動並更新", later: "稍後",
    cancelUpdateWait: "取消等待更新", updateDisabled: "此版本未提供自動更新。",
    updateIdle: "可隨時檢查更新。", updateChecking: "正在檢查更新…",
    updateAvailable: "有新版本可下載。", updateDownloading: "正在下載更新…",
    updateDownloaded: "更新已下載完成。", updateWaiting: "正在等待工作完成…",
    updateInstalling: "正在安裝更新…", updateError: "無法完成更新，請稍後重試。",
    updateBackupError: "無法備份本地資料。請檢查磁碟空間與資料目錄權限後再試一次。",
    updateCancelError: "無法解除更新等待。請重新開啟應用後再試一次。",
    updateIdleDetail: "應用會定期檢查更新，您也可以隨時手動檢查。",
    updateDownloadedDetail: "重新啟動前會先等待分析與其他工作完成，並備份本地資料。",
    updateWaitingDetail: "正在進行的工作會繼續執行，完成後會自動備份並安裝更新。您可以取消等待。",
    updateActiveTasks: "仍在執行的工作", updateInstalledVersion: "目前版本", updateNewVersion: "新版本",
    edit: "編輯", view: "顯示方式", window: "視窗",
    starting: "正在啟動", failed: "本地後端未能啟動",
    preparing: "正在準備本地服務與資料庫。",
    failureBody: "無法準備本地資料庫或啟動服務。請檢查資料目錄是否可寫入，以及是否有足夠的磁碟空間。",
    failureHint: "選單「設定 → 開啟資料目錄」可查看資料；「開啟後端記錄」可查看錯誤原因。",
    retry: "重新啟動", retryFailed: "無法重新啟動，請查看後端記錄。",
    newerTitle: "需要更新 txinTrade",
    newerBody: "你的資料來自較新版本的 txinTrade，這個版本無法開啟。請更新到最新版，資料不會遺失。",
    newerHint: "更新前會先備份本地資料。",
    checkNow: "檢查更新", downloadLatest: "下載最新版",
  },
  "en-US": {
    settings: "Settings", openData: "Open Data Folder", openLog: "Open Backend Log",
    about: "About txinTrade", changelog: "Changelog", close: "Close",
    releaseChannel: "Release channel", stableChannel: "Stable", betaChannel: "Beta",
    unreleasedBuild: "This is a development build.", noReleaseNotes: "No release notes are included for this version.",
    checkUpdates: "Check for Updates", updateStatus: "Update Status", installUpdate: "Install Update",
    downloadUpdate: "Download Update", restartNow: "Restart and Update", later: "Later",
    cancelUpdateWait: "Cancel Update Wait", updateDisabled: "Automatic updates are unavailable in this build.",
    updateIdle: "You can check for updates at any time.", updateChecking: "Checking for updates…",
    updateAvailable: "A new version is available.", updateDownloading: "Downloading the update…",
    updateDownloaded: "The update is ready to install.", updateWaiting: "Waiting for work to finish…",
    updateInstalling: "Installing the update…", updateError: "The update could not finish. Please try again later.",
    updateBackupError: "The local data backup failed. Check disk space and data folder permissions, then try again.",
    updateCancelError: "The update wait could not be cancelled. Reopen the application and try again.",
    updateIdleDetail: "The application checks periodically. You can also check manually at any time.",
    updateDownloadedDetail: "Before restarting, the application waits for analysis and other work to finish and backs up local data.",
    updateWaitingDetail: "Current work will continue. Once it finishes, the application will back up data and install the update. You can cancel the wait.",
    updateActiveTasks: "Work still running", updateInstalledVersion: "Installed version", updateNewVersion: "New version",
    edit: "Edit", view: "View", window: "Window",
    starting: "Starting up", failed: "The local service could not start",
    preparing: "Preparing the local service and database.",
    failureBody: "The local database or service could not start. Check that the data folder is writable and that enough disk space is available.",
    failureHint: "Use Settings → Open Data Folder to view your data, or Open Backend Log to check the error.",
    retry: "Restart", retryFailed: "Restart failed. Check the backend log for details.",
    newerTitle: "Update txinTrade",
    newerBody: "Your data comes from a newer version of txinTrade, which this version cannot open. Update to the latest version; your data is kept.",
    newerHint: "Local data is backed up before the update.",
    checkNow: "Check for Updates", downloadLatest: "Download the Latest Version",
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
