import { uiText } from "./i18n/index.ts";
export type DesktopBridge = {
  openExternal: (url: string) => Promise<void>;
  updateLocale?: (locale: import("./i18n/index.ts").UiLocale) => Promise<void>;
  updateTheme?: (theme: import("./uiTheme.ts").UiTheme) => Promise<void>;
  platform?: string;
  version?: string;
};

declare global {
  interface Window {
    tradeHelper?: DesktopBridge;
  }
}

export async function openAuthorization(url: string) {
  const destination = new URL(url);
  if (destination.protocol !== "https:" && destination.protocol !== "http:") {
    throw new Error(uiText("授權連結格式不正確"));
  }
  if (window.tradeHelper) {
    await window.tradeHelper.openExternal(destination.href);
    return;
  }
  const tab = window.open(destination.href, "_blank", "noopener,noreferrer");
  // Browsers may return null for a successfully opened noopener tab.
  if (tab) tab.opener = null;
}
