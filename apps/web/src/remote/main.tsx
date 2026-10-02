import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "../index.css";
import "../App.css";
import "../Workspace.css";
import "../SettingsPanel.css";
import { isUiLocale, setUiLocale } from "../i18n/index.ts";
import { browserPreference } from "../browserPreferences.ts";
import { applyUiTheme, initializeUiTheme, isUiTheme } from "../uiTheme.ts";
import RemoteApp from "./RemoteApp.tsx";
import "../MobileLayout.css";

initializeUiTheme();
// The remote page keeps its own appearance and language, defaulting to the browser's.
const theme = browserPreference("theme");
if (isUiTheme(theme)) applyUiTheme(theme);
const locale = browserPreference("locale");
void setUiLocale(isUiLocale(locale) ? locale : navigator.language.toLowerCase().startsWith("zh") ? "zh-TW" : "en-US");

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <RemoteApp />
  </StrictMode>,
);
