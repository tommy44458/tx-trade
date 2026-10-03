import { uiText } from "./i18n/index.ts";
/** The update state the desktop shell shares with the window; null when updates are off. */
export type DesktopUpdateState = {
  status: "idle" | "checking" | "available" | "downloading" | "downloaded" | "waiting-for-idle" | "installing" | "error";
  version: string | null;
  percent: number;
  canInstall: boolean;
} | null;

/** What the update window shows, already in the app's language (built by the desktop shell). */
export type DesktopUpdatePrompt = {
  tone: "info" | "warning";
  title: string;
  message: string;
  details: string[];
  notes: string;
  action: "check" | "download" | "install" | "cancel" | null;
  actionLabel: string | null;
  dismissLabel: string;
};

export type DesktopBridge = {
  updatePromptReady?: () => Promise<void>;
  updateRespond?: (action: NonNullable<DesktopUpdatePrompt["action"]>) => Promise<void>;
  onUpdatePrompt?: (listener: (prompt: DesktopUpdatePrompt) => void) => () => void;
  updateState?: () => Promise<DesktopUpdateState>;
  showUpdate?: () => Promise<void>;
  onUpdateState?: (listener: (state: DesktopUpdateState) => void) => () => void;
  openExternal: (url: string) => Promise<void>;
  /** The page has no clipboard permission in the desktop app; the shell copies for it. */
  copyText?: (text: string) => Promise<void>;
  focusWindow?: () => Promise<void>;
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
